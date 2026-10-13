import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from src.errors import ComicApiError
from src.services.aggregator import AggregatorService
from src.services.favorites_library_sync import CATEGORIES, FavoritesLibrarySettings, FavoritesLibrarySync
from src.sources import SourcePlugin
from src.storage import Store


class LibraryFavoritesPlugin(SourcePlugin):
    capabilities = ["search", "detail", "pages", "favorites", "login"]
    login_fields = [{"name": "account", "label": "账号"}, {"name": "password", "label": "密码", "type": "password"}]

    def __init__(self, store, source):
        super().__init__(store)
        self.id, self.name = source, CATEGORIES[source]
        self.ids = [self.comic_id(1), self.comic_id(2)]
        self.calls, self.details = [], []
        self.broken = set()
        self.block = None
        self.entered, self.release = threading.Event(), threading.Event()

    def comic_id(self, value):
        return str(value) if self.id == "jm" else f"{value:024x}"

    def wait(self, operation):
        if self.block == operation:
            self.entered.set()
            if not self.release.wait(5):
                raise TimeoutError("Test did not release request")

    def favorites(self, page=1, **kwargs):
        self.calls.append((page, kwargs))
        self.wait("favorites")
        return {"items": [{"id": value, "title": f"测试漫画 {value}"} for value in self.ids[page-1:page]],
                "page": page, "page_size": 1, "total": len(self.ids), "has_next": page < len(self.ids)}

    def detail(self, comic_id):
        self.details.append(comic_id)
        self.wait("detail")
        if comic_id in self.broken:
            raise ComicApiError("模拟详情失败", 502, "upstream_error", self.id)
        return {"id": comic_id, "title": f"测试漫画 {comic_id}", "author": "测试作者",
                "chapters": [{"id": comic_id, "name": "第一章"}]}

    def pages(self, *args):
        raise AssertionError("Library sync must not fetch images")

    def search(self, keyword, page=1):
        return []

    def account_status(self):
        return {"configured": bool((self.store.account(self.id) or {}).get("account"))}

    def login(self, values):
        self.store.save_account(self.id, values)

    def logout(self):
        self.store.save_account(self.id, {})


def make_sync(tmp_path):
    store = Store(tmp_path)
    plugins = {source: LibraryFavoritesPlugin(store, source) for source in CATEGORIES}
    for plugin in plugins.values():
        plugin.login({"account": "test-owner", "password": "test-password"})
    return FavoritesLibrarySync(store, AggregatorService(store, plugins)), plugins


async def run_once(sync, source):
    sync.launch(source)
    await sync.tasks[source]
    return sync.status(source)["current"]


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(.01)
    await asyncio.wait_for(wait(), 3)


def test_default_off_without_categories_or_network(tmp_path):
    async def scenario():
        sync, plugins = make_sync(tmp_path)
        await sync.start()
        await asyncio.sleep(.02)
        await sync.stop()
        assert sync.store.books() == sync.store.library_categories() == []
        for source, plugin in plugins.items():
            assert sync.status(source)["settings"] == {"enabled": False, "interval_minutes": 60}
            assert plugin.calls == plugin.details == []
    asyncio.run(scenario())


@pytest.mark.parametrize("source", CATEGORIES)
def test_full_scan_adds_only_metadata_preserving_manual_categories(tmp_path, source):
    async def scenario():
        sync, plugins = make_sync(tmp_path)
        plugin = plugins[source]
        old_id = plugin.ids[0]
        sync.store.save_book(source, old_id, {"title": "已缓存详情", "chapters": [{"id": "original"}]}, "手动追更")
        sync.save_settings(source, FavoritesLibrarySettings(enabled=True))
        assert sync.store.library_categories() == sorted([CATEGORIES[source], "手动追更"])
        state = await run_once(sync, source)
        assert state["status"] == "completed" and state["added"] == 1 and state["total"] == 2
        books = {book["id"]: book for book in sync.store.books()}
        assert books[old_id]["title"] == "已缓存详情"
        assert books[old_id]["library_category"] == "手动追更"
        assert books[old_id]["library_categories"] == sorted(["手动追更", CATEGORIES[source]])
        assert books[plugin.ids[1]]["library_categories"] == [CATEGORIES[source]]
        assert plugin.details == [plugin.ids[1]]
        assert plugin.calls == [(page, {"folder_id": "0", "order": "mr"} if source == "jm" else {"sort": "dd"}) for page in (1, 2)]
        assert sync.store.tasks() == []
        state = await run_once(sync, source)
        assert state["added"] == 0 and len(sync.store.books()) == 2
        assert plugin.details == [plugin.ids[1]]
        plugin.ids = []
        await run_once(sync, source)
        sync.save_settings(source, FavoritesLibrarySettings(enabled=False))
        assert len(sync.store.books()) == 2
        assert CATEGORIES[source] in Store(tmp_path).library_categories()
    asyncio.run(scenario())


@pytest.mark.parametrize("source", CATEGORIES)
def test_empty_favorites_create_persistent_category_on_enable(tmp_path, source):
    async def scenario():
        sync, plugins = make_sync(tmp_path)
        plugins[source].ids = []
        sync.save_settings(source, FavoritesLibrarySettings(enabled=True))
        assert sync.store.library_categories() == [CATEGORIES[source]]
        assert (await run_once(sync, source))["status"] == "completed"
        restarted = FavoritesLibrarySync(Store(tmp_path), sync.service)
        assert restarted.settings(source)["enabled"] is True
        assert restarted.status(source)["next_run_at"] > time.time()
        assert restarted.store.library_categories() == [CATEGORIES[source]]
        assert restarted.store.books() == []
    asyncio.run(scenario())


@pytest.mark.parametrize("source", CATEGORIES)
def test_failed_details_retry_next_round_without_overwriting_successes(tmp_path, source):
    async def scenario():
        sync, plugins = make_sync(tmp_path)
        plugin = plugins[source]
        plugin.broken.add(plugin.ids[0])
        sync.save_settings(source, FavoritesLibrarySettings(enabled=True))
        state = await run_once(sync, source)
        assert state["status"] == "partial" and state["failed"] == 1 and state["added"] == 1
        assert state["errors"][0]["comic_id"] == plugin.ids[0]
        plugin.broken.clear()
        assert (await run_once(sync, source))["status"] == "completed"
        assert len(sync.store.books()) == 2
        assert plugin.details.count(plugin.ids[1]) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("source", CATEGORIES)
@pytest.mark.parametrize("failure", ["duplicate", "changed", "incomplete", "empty", "network"])
def test_invalid_scan_does_not_import_partial_favorites(tmp_path, monkeypatch, source, failure):
    async def scenario():
        sync, plugins = make_sync(tmp_path)
        plugin = plugins[source]
        original = plugin.favorites
        def favorites(page=1, **kwargs):
            data = original(page, **kwargs)
            if failure == "duplicate" and page == 2:
                data["items"] = [{"id": plugin.ids[0]}]
            if failure == "changed" and page == 2:
                data["total"] = 3
            if failure == "incomplete":
                data["has_next"] = False
            if failure == "empty":
                data["items"] = []
            if failure == "network" and page == 2:
                raise ComicApiError("分页失败", 502, "upstream_error", source)
            return data
        monkeypatch.setattr(plugin, "favorites", favorites)
        sync.save_settings(source, FavoritesLibrarySettings(enabled=True))
        state = await run_once(sync, source)
        assert state["status"] == "failed" and state["error"]
        assert sync.store.books() == [] and plugin.details == []
        assert sync.status(source)["next_run_at"] > time.time()
    asyncio.run(scenario())


@pytest.mark.parametrize("source", CATEGORIES)
@pytest.mark.parametrize("operation", ["favorites", "detail"])
@pytest.mark.parametrize("action", ["disable", "source_off", "account", "owner_changed", "shutdown"])
def test_inflight_results_do_not_write_after_stop_or_account_change(tmp_path, source, operation, action):
    async def scenario():
        sync, plugins = make_sync(tmp_path)
        plugin = plugins[source]
        plugin.block = operation
        sync.save_settings(source, FavoritesLibrarySettings(enabled=True))
        sync.launch(source)
        await wait_until(plugin.entered.is_set)
        if action == "disable":
            sync.save_settings(source, FavoritesLibrarySettings())
        elif action == "source_off":
            sync.service.set_enabled(source, False)
            sync.source_changed(source)
        elif action == "account":
            async with sync.account_change(source):
                plugin.login({"account": "another-owner"})
        elif action == "owner_changed":
            plugin.login({"account": "another-owner"})
        else:
            stopped = asyncio.create_task(sync.stop())
            await asyncio.sleep(0)
            assert not stopped.done()
        plugin.release.set()
        await sync.tasks[source]
        if action == "shutdown":
            await stopped
        assert sync.store.books() == []
        if action in ("disable", "account", "owner_changed"):
            assert sync.settings(source)["enabled"] is False
        if action == "source_off":
            assert sync.settings(source)["enabled"] is True and sync.status(source)["paused"] is True
    asyncio.run(scenario())


def test_scheduler_runs_independently_and_restores_plan_after_restart(tmp_path, monkeypatch):
    async def scenario():
        sync, plugins = make_sync(tmp_path)
        def fail(**kwargs):
            raise ComicApiError("JM 不可用", 502, "upstream_error", "jm")
        monkeypatch.setattr(plugins["jm"], "favorites", fail)
        for source in CATEGORIES:
            sync.save_settings(source, FavoritesLibrarySettings(enabled=True, interval_minutes=5))
        await sync.start()
        await wait_until(lambda: sync.states["jm"]["status"] == "failed" and sync.states["bika"]["status"] == "completed")
        assert len(sync.store.books()) == 2
        await sync.stop()
        restarted = FavoritesLibrarySync(Store(tmp_path), sync.service)
        calls = len(plugins["bika"].calls)
        await restarted.start()
        await asyncio.sleep(.02)
        assert len(plugins["bika"].calls) == calls
        plugins["bika"].ids.append(plugins["bika"].comic_id(3))
        restarted.states["bika"]["next_run_at"] = 0
        restarted.wake.set()
        await wait_until(lambda: len(sync.store.books()) == 3)
        await restarted.stop()
    asyncio.run(scenario())


def test_reenable_during_inflight_request_does_not_accept_old_generation(tmp_path):
    async def scenario():
        sync, plugins = make_sync(tmp_path)
        plugins["jm"].block = "detail"
        sync.save_settings("jm", FavoritesLibrarySettings(enabled=True))
        sync.launch("jm")
        await wait_until(plugins["jm"].entered.is_set)
        sync.save_settings("jm", FavoritesLibrarySettings())
        sync.save_settings("jm", FavoritesLibrarySettings(enabled=True))
        plugins["jm"].release.set()
        await sync.tasks["jm"]
        assert sync.store.books() == [] and sync.states["jm"]["next_run_at"] == 0
        assert (await run_once(sync, "jm"))["added"] == 2
    asyncio.run(scenario())


def test_source_pause_resume_and_restart_interrupted_task(tmp_path):
    async def scenario():
        sync, plugins = make_sync(tmp_path)
        sync.save_settings("jm", FavoritesLibrarySettings(enabled=True))
        sync.service.set_enabled("jm", False)
        sync.source_changed("jm")
        await sync.start()
        await asyncio.sleep(.02)
        assert plugins["jm"].calls == []
        sync.service.set_enabled("jm", True)
        sync.source_changed("jm")
        await wait_until(lambda: sync.states["jm"]["status"] == "completed")
        await sync.stop()
        sync.store.save_settings("library_sync_state:jm", {"status": "running", "next_run_at": time.time() + 500})
        restarted = FavoritesLibrarySync(Store(tmp_path), sync.service)
        await restarted.start()
        await wait_until(lambda: restarted.states["jm"]["status"] == "completed")
        await restarted.stop()
    asyncio.run(scenario())


def test_collection_memberships_survive_manual_updates_and_migration(tmp_path):
    store = Store(tmp_path)
    store.save_book("jm", "1", {"title": "旧书"}, "追更")
    with store.connect() as db:
        db.execute("DROP TABLE library_category_members")
        db.execute("DROP TABLE library_categories")
        db.execute("PRAGMA user_version=5")
    store = Store(tmp_path)
    assert store.books()[0]["library_category"] == "追更"
    store.import_favorite_book("jm", "1", {"title": "不覆盖"}, CATEGORIES["jm"])
    store.set_category("jm", "1", "已读")
    assert store.books()[0]["library_categories"] == sorted(["已读", "JM 收藏"])
    store.save_book("jm", "1", {"title": "已刷新"}, "已读")
    assert store.books()[0]["library_categories"] == sorted(["已读", "JM 收藏"])
    store.remove_book("jm", "1")
    store.save_book("jm", "1", {"title": "手动重加"})
    assert store.books()[0]["library_categories"] == []
    assert store.library_categories() == ["JM 收藏"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "default"))
    from main import create_app
    sync, plugins = make_sync(tmp_path / "app")
    with TestClient(create_app(sync.store, plugins)) as client:
        yield client


def wait_for_api_sync(client, source):
    for _ in range(300):
        status = client.get(f"/api/sources/{source}/library-sync").json()
        if status["current"]["status"] == "completed":
            return status
        time.sleep(.01)
    pytest.fail(f"Sync did not finish: {status}")


@pytest.mark.parametrize("source", CATEGORIES)
def test_api_settings_categories_and_account_reset(client, source):
    path = f"/api/sources/{source}/library-sync"
    response = client.get(path)
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["settings"]["enabled"] is False
    assert client.post(path + "/run").json()["error"]["code"] == "sync_disabled"
    assert client.put(path, json={"enabled": True}).status_code == 200
    wait_for_api_sync(client, source)
    books = client.get("/api/library", params={"category": CATEGORIES[source]}).json()
    assert len(books["items"]) == 2 and books["categories"] == [CATEGORIES[source]]
    first = books["items"][0]
    book_path = f'/api/library/{source}/{first["id"]}'
    client.patch(book_path, json={"category": "手动追更"})
    assert len(client.get("/api/library", params={"category": "手动追更"}).json()["items"]) == 1
    assert len(client.get("/api/library", params={"category": CATEGORIES[source]}).json()["items"]) == 2
    assert client.get("/api/library?category=").json()["items"] == []
    assert "owner" not in client.get(path).text and "test-password" not in client.get(path).text
    assert client.post(f"/api/sources/{source}/login", json={"account": "changed", "password": "changed"}).status_code == 200
    assert client.get(path).json()["settings"]["enabled"] is False
    assert len(client.get("/api/library").json()["items"]) == 2
    assert client.put(path, json={"enabled": True}).status_code == 200
    assert client.delete(f"/api/sources/{source}/account").status_code == 200
    assert client.get(path).json()["settings"]["enabled"] is False
    assert client.put(path, json={"enabled": True}).status_code == 401


@pytest.mark.parametrize("values", [{"enabled": "true"}, {"enabled": 1}, {"interval_minutes": 0},
                                    {"interval_minutes": 10081}, {"interval_minutes": "60"}, {"owner": "forged"}])
def test_api_rejects_invalid_settings(client, values):
    assert client.put("/api/sources/jm/library-sync", json=values).status_code == 422
    assert client.app.state.store.library_categories() == []


def test_disabled_source_can_read_and_disable_library_sync(client):
    path = "/api/sources/jm/library-sync"
    client.put("/api/sources/jm/state", json={"enabled": False})
    assert client.get(path).json()["paused"] is True
    assert client.put(path, json={"enabled": True}).json()["error"]["code"] == "source_disabled"
    assert client.put(path, json={"enabled": False}).status_code == 200
    assert client.get("/api/sources/missing/library-sync").status_code == 404
