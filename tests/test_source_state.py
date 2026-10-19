import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.errors import ComicApiError
from src.services.aggregator import AggregatorService
from src.services.downloads import DownloadManager
from src.sources.copy import CopyPlugin
from src.storage import Store
from tests.fakes import DemoPlugin


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "default-data"))
    store = Store(tmp_path / "store")
    first, second = DemoPlugin(store), DemoPlugin(store)
    second.id = "second"
    first.login({"account": "demo", "password": "test-only"})
    store.save_book("demo", "book", first.detail("book"), "分类")
    return store, {"demo": first, "second": second}


def test_default_enabled_and_persistence_are_separate_from_accounts(setup):
    store, plugins = setup
    service = AggregatorService(store, plugins)
    assert service.manifest("demo")["enabled"] is True
    account = store.account("demo")
    service.set_enabled("demo", False)
    assert service.manifest("demo")["enabled"] is False
    assert service.manifest("demo")["account"]["authenticated"]
    assert service.source("demo", allow_disabled=True) is plugins["demo"]
    assert service.is_enabled("second")
    assert store.account("demo") == account and store.books()[0]["library_category"] == "分类"
    restarted = AggregatorService(Store(store.root), plugins)
    assert not restarted.is_enabled("demo")
    restarted.set_enabled("demo", True)
    assert AggregatorService(Store(store.root), plugins).is_enabled("demo")


def test_aggregate_excludes_disabled_sources_and_all_disabled_is_empty(setup):
    store, plugins = setup
    service = AggregatorService(store, plugins)
    service.set_enabled("demo", False)
    plugins["demo"].search = lambda *args: pytest.fail("disabled source called")
    result = asyncio.run(service.aggregate_search("book"))
    assert set(result["all_results"]) == {"second"} and result["errors"] == {}
    service.set_enabled("second", False)
    result = asyncio.run(service.aggregate_search("book"))
    assert result["items"] == [] and result["all_results"] == {} and result["best_match"] is None


def test_source_api_gates_upstream_but_keeps_management_and_local_data(setup):
    from main import create_app
    store, plugins = setup
    with TestClient(create_app(store, plugins)) as client:
        response = client.put("/api/sources/demo/state", json={"enabled": False})
        assert response.status_code == 200 and response.json()["source"]["enabled"] is False
        manifest = client.get("/api/sources")
        assert manifest.headers["cache-control"] == "no-store"
        assert len(manifest.json()["sources"]) == 2
        assert manifest.json()["sources"][0]["enabled"] is False
        for path in ("/api/search?keyword=book&source=demo", "/api/demo/latest", "/api/comic/demo/book",
                     "/api/chapter/demo/book/1", "/api/image/proxy?source=demo&url=https://images.invalid/1.png"):
            result = client.get(path)
            assert result.status_code == 409, path
            assert result.json()["error"]["code"] == "source_disabled"
        assert client.post("/api/sources/demo/login", json={"account": "demo", "password": "test"}).status_code == 409
        assert client.post("/api/downloads", json={"source": "demo", "comic_id": "book", "chapter_id": "1"}).status_code == 409
        assert client.post("/api/library/demo/book/refresh").status_code == 409
        assert len(client.get("/api/library").json()["items"]) == 1
        assert client.patch("/api/library/demo/book", json={"category": "保留"}).status_code == 200
        assert client.delete("/api/sources/demo/account").status_code == 200
        assert not client.app.state.service.is_enabled("demo")
        assert client.put("/api/sources/demo/state", json={"enabled": True}).status_code == 200
        assert client.get("/api/comic/demo/book").status_code == 200


@pytest.mark.parametrize("body", [{}, {"enabled": "false"}, {"enabled": 0}, {"enabled": None}, {"enabled": []}])
def test_source_toggle_requires_real_boolean(setup, body):
    from main import create_app
    store, plugins = setup
    with TestClient(create_app(store, plugins)) as client:
        assert client.put("/api/sources/demo/state", json=body).status_code == 422
        assert client.app.state.service.is_enabled("demo")
        assert client.put("/api/sources/missing/state", json={"enabled": False}).status_code == 404


def test_settings_adapter_replacement_does_not_reenable_source(setup):
    from main import create_app
    store, plugins = setup
    plugins["copy"] = CopyPlugin(store)
    with TestClient(create_app(store, plugins)) as client:
        assert client.put("/api/sources/copy/state", json={"enabled": False}).status_code == 200
        result = client.put("/api/sources/copy/settings", json={"platform": "3"})
        assert result.status_code == 200 and not result.json()["source"]["enabled"]
        assert result.json()["source"]["settings"]["platform"] == "3"
        assert client.get("/api/copy/latest").status_code == 409


def test_call_waiting_for_source_slot_does_not_run_after_disabled(setup):
    async def scenario():
        store, plugins = setup
        service = AggregatorService(store, plugins)
        release = threading.Event()
        started = []

        def slow():
            started.append(True)
            release.wait(3)
            return []

        plugins["demo"].slow = slow
        calls = [asyncio.create_task(service.call("demo", "slow")) for _ in range(3)]
        try:
            for _ in range(100):
                if len(started) == 2:
                    break
                await asyncio.sleep(.01)
            assert len(started) == 2
            service.set_enabled("demo", False)
        finally:
            release.set()
        results = await asyncio.gather(*calls, return_exceptions=True)
        assert len(started) == 2
        assert isinstance(results[2], ComicApiError) and results[2].code == "source_disabled"
        assert service._slots["demo"]._value == 2
    asyncio.run(scenario())


def test_aggregate_discards_response_from_source_disabled_while_running(setup):
    async def scenario():
        store, plugins = setup
        service = AggregatorService(store, plugins)
        entered, release = threading.Event(), threading.Event()

        def slow(*args):
            entered.set()
            release.wait(3)
            return [{"id": "old", "title": "旧结果"}]

        plugins["demo"].search = slow
        task = asyncio.create_task(service.aggregate_search("book"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            service.set_enabled("demo", False)
        finally:
            release.set()
        result = await task
        assert set(result["all_results"]) == {"second"} and result["errors"] == {}
    asyncio.run(scenario())


def test_image_waiting_for_slot_never_downloads_after_disabled(setup, monkeypatch):
    async def scenario():
        store, plugins = setup
        service = AggregatorService(store, plugins)
        service._image_proxy._slots = asyncio.Semaphore(0)
        monkeypatch.setattr("src.services.image_proxy.download_image", lambda *args: pytest.fail("disabled image requested"))
        task = asyncio.create_task(service.image("demo", "https://images.invalid/1.png"))
        await asyncio.sleep(.02)
        service.set_enabled("demo", False)
        service._image_proxy._slots.release()
        with pytest.raises(ComicApiError) as error:
            await task
        assert error.value.code == "source_disabled"
    asyncio.run(scenario())


def test_disable_cancels_only_selected_source_downloads_and_blocks_retry(setup):
    async def scenario():
        store, plugins = setup
        service = AggregatorService(store, plugins)
        manager = DownloadManager(store, service)
        first = manager.create("demo", "book", "1")
        second = manager.create("second", "book", "1")
        service.set_enabled("demo", False)
        manager.cancel_source("demo")
        assert store.task(first["id"])["status"] == "cancelled"
        assert store.task(second["id"])["status"] == "queued"
        manager.worker = asyncio.create_task(manager.run())
        await manager.queue.join()
        assert store.task(first["id"])["status"] == "cancelled"
        assert store.task(second["id"])["status"] == "completed"
        with pytest.raises(ComicApiError) as error:
            manager.retry(first["id"])
        assert error.value.code == "source_disabled"
        assert manager.file_path(second["id"]).is_file()
        service.set_enabled("demo", True)
        manager.retry(first["id"])
        await manager.queue.join()
        assert store.task(first["id"])["status"] == "completed"
        await manager.stop()
    asyncio.run(scenario())


def test_disable_during_queued_business_call_marks_download_cancelled(setup):
    async def scenario():
        store, plugins = setup
        service = AggregatorService(store, plugins)
        slot = service._slots["demo"]
        await slot.acquire()
        await slot.acquire()
        manager = DownloadManager(store, service)
        await manager.start()
        task = manager.create("demo", "book", "1")
        await asyncio.sleep(.03)
        assert store.task(task["id"])["status"] == "running"
        service.set_enabled("demo", False)
        manager.cancel_source("demo")
        slot.release()
        slot.release()
        await manager.queue.join()
        assert store.task(task["id"])["status"] == "cancelled"
        await manager.stop()
    asyncio.run(scenario())


def test_image_proxy_lets_browser_reuse_covers_without_site_auth(setup):
    from main import create_app
    store, plugins = setup
    with TestClient(create_app(store, plugins)) as client:
        image = client.get("/api/image/proxy?source=demo&url=https://images.invalid/1.png")
        assert image.status_code == 200
        assert image.headers["cache-control"] == "private, max-age=604800"
