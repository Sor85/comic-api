import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from main import create_app
from src.storage import Store
from tests.fakes import DemoPlugin
from tests.test_jm_sync import FavoritePlugin


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "default-data"))
    monkeypatch.delenv("LIBRARY_DOWNLOAD_ROOTS", raising=False)
    monkeypatch.setattr("src.services.local_images.ensure_public_url", lambda url: None)


LIBRARY_SETTINGS = {"enabled": False, "cron": "0 * * * *", "download_dir": "", "speed_limit_kbps": 0,
                    "image_format": "original", "quality": 90, "directory_rule": "id", "retries": 0}


def use_id_rule(client):
    assert client.put("/api/library/sync/settings", json=LIBRARY_SETTINGS).status_code == 200


def library_root(client):
    return Path(client.app.state.library_images.settings()["download_dir"])


def wait_for_sync(client):
    for _ in range(500):
        data = client.get("/api/library/sync").json()
        if not data["active"]:
            return data["history"][0]
        time.sleep(.01)
    pytest.fail("library sync did not finish")


def test_library_sync_reuses_source_worker_and_skips_verified_album(tmp_path):
    store = Store(tmp_path)
    plugin = FavoritePlugin(store)
    store.save_book("jm", "101", plugin.detail("101"))
    with TestClient(create_app(store, {"jm": plugin})) as client:
        use_id_rule(client)
        assert client.post("/api/library/sync/run").status_code == 202
        first = wait_for_sync(client)
        assert first["status"] == "completed", first
        assert first["downloaded"] == 4 and first["skipped"] == 0
        assert library_root(client) == (tmp_path / "library").resolve()
        assert (library_root(client) / "jm" / "101" / "1011" / "00001.png").is_file()

        plugin.requests.clear()
        assert client.post("/api/library/sync/run").status_code == 202
        second = wait_for_sync(client)
        assert second["status"] == "completed", second
        assert second["downloaded"] == 0 and second["skipped"] == 4
        assert not plugin.requests

        # A new upstream chapter invalidates the whole-book fast path; old pages remain skipped.
        plugin.extra_chapter = True
        plugin.requests.clear()
        assert client.post("/api/library/sync/run").status_code == 202
        third = wait_for_sync(client)
        assert third["status"] == "completed", third
        assert third["downloaded"] == 2 and third["skipped"] == 4
        assert len(plugin.requests) == 2


def test_library_checkpoint_tables_are_created_per_source_on_demand(tmp_path):
    store = Store(tmp_path)
    plugin = FavoritePlugin(store)
    store.save_book("jm", "101", plugin.detail("101"))

    def tables():
        with store.connect() as db:
            return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    with TestClient(create_app(store, {"jm": plugin})) as client:
        assert not {name for name in tables() if name.startswith("library_jm_")}
        use_id_rule(client)
        assert client.post("/api/library/sync/run").status_code == 202
        assert wait_for_sync(client)["status"] == "completed"
    assert {"library_jm_sync_runs", "library_jm_sync_files", "library_jm_sync_local_files",
            "library_jm_local_status"} <= tables()


def test_library_sync_options_list_categories_with_counts(tmp_path):
    store = Store(tmp_path)
    plugin = FavoritePlugin(store)
    store.save_book("jm", "101", plugin.detail("101"), "精选")
    store.save_book("jm", "102", plugin.detail("102"))
    store.import_favorite_book("jm", "102", plugin.detail("102"), "JM 收藏")
    store.save_book("jm", "103", plugin.detail("103"))
    with TestClient(create_app(store, {"jm": plugin})) as client:
        options = client.get("/api/library/sync/settings").json()
        assert {item["value"]: item["books"] for item in options["categories"]} == {"JM 收藏": 1, "精选": 1, "": 1}
        assert options["books"] == options["scope_books"] == 3
        response = client.put("/api/library/sync/settings", json=dict(options["settings"], categories=["JM 收藏", ""]))
        assert response.status_code == 200 and response.json()["scope_books"] == 2
        assert client.put("/api/library/sync/settings", json=dict(options["settings"], categories=["x" * 101])).status_code == 422


def test_library_sync_reports_failed_worker_run_as_partial(tmp_path):
    from src.errors import ComicApiError
    store = Store(tmp_path)
    plugin = FavoritePlugin(store)
    store.save_book("jm", "101", plugin.detail("101"))

    def expired(comic_id):
        raise ComicApiError("登录已失效", 401, "login_required", "jm")
    plugin.detail = expired
    with TestClient(create_app(store, {"jm": plugin})) as client:
        assert client.post("/api/library/sync/run").status_code == 202
        run = wait_for_sync(client)
    assert run["status"] == "partial", run
    assert run["failed"] >= 1 and run["albums_done"] == run["albums_total"] == 1
    assert any("登录已失效" in error["message"] for error in run["errors"])


def test_invalid_library_id_fails_only_that_book(tmp_path):
    store = Store(tmp_path)
    plugin = FavoritePlugin(store)
    store.save_book("jm", "101", plugin.detail("101"))
    store.save_book("jm", "../escape", {"title": "坏编号"})
    with TestClient(create_app(store, {"jm": plugin})) as client:
        use_id_rule(client)
        assert client.post("/api/library/sync/run").status_code == 202
        run = wait_for_sync(client)
    assert run["status"] == "partial", run
    assert run["failed"] == 1 and run["downloaded"] == 4
    assert run["albums_done"] == run["albums_total"] == 2


def test_shutdown_stops_running_worker_and_live_progress_is_reported(tmp_path):
    store = Store(tmp_path)
    plugin = FavoritePlugin(store)
    plugin.page_count, plugin.delay = 40, .1
    store.save_book("jm", "101", plugin.detail("101"))
    with TestClient(create_app(store, {"jm": plugin})) as client:
        use_id_rule(client)
        assert client.post("/api/library/sync/run").status_code == 202
        for _ in range(300):
            current = client.get("/api/library/sync").json()["current"]
            if current["page"]:
                break
            time.sleep(.01)
        assert current["title"] == "漫画 101" and current["pages"] == 40
        started = time.monotonic()
    # 80 pages at 0.1 s each would take 8 s if shutdown waited for the worker.
    assert time.monotonic() - started < 3
    run = store.settings("library_image_sync_history")[0]
    assert run["status"] == "interrupted", run


def test_disabling_another_source_keeps_running_worker(tmp_path):
    store = Store(tmp_path)
    plugin = FavoritePlugin(store)
    plugin.page_count, plugin.delay = 20, .05
    store.save_book("jm", "101", plugin.detail("101"))
    other = DemoPlugin(store)
    with TestClient(create_app(store, {"jm": plugin, other.id: other})) as client:
        use_id_rule(client)
        assert client.post("/api/library/sync/run").status_code == 202
        for _ in range(300):
            if client.app.state.library_images.child:
                break
            time.sleep(.01)
        worker = client.app.state.library_images.child
        assert worker.source == "jm" and worker.active
        assert client.put(f"/api/sources/{other.id}/state", json={"enabled": False}).status_code == 200
        assert worker.active
        run = wait_for_sync(client)
    assert run["status"] == "completed", run


def test_completion_records_are_stored_per_album(tmp_path):
    store = Store(tmp_path)
    plugin = FavoritePlugin(store)
    store.save_book("jm", "101", plugin.detail("101"))
    store.save_book("jm", "102", plugin.detail("102"))
    with TestClient(create_app(store, {"jm": plugin})) as client:
        use_id_rule(client)
        assert client.post("/api/library/sync/run").status_code == 202
        assert wait_for_sync(client)["status"] == "completed"
    with store.connect() as db:
        names = [row[0] for row in db.execute("SELECT name FROM app_settings WHERE name LIKE 'image_sync_complete%'")]
    assert len(names) == 2 and all(name.startswith("image_sync_complete:jm:") for name in names)


class OtherPlugin(DemoPlugin):
    id = "other"
    name = "其他图源"
    capabilities = ["search", "detail", "pages"]

    def __init__(self, store):
        super().__init__(store)
        self.requests = []
        self.client.stream = self.stream

    @contextmanager
    def stream(self, method, url, **kwargs):
        self.requests.append(url)
        response = SimpleNamespace(status_code=200)
        response.iter_content = lambda: iter([self.picture])
        yield response

    def pages(self, comic_id, chapter_id):
        return [f"https://demo.invalid/{chapter_id}/{index}.png" for index in range(3)]


def test_library_sync_covers_sources_without_favorites(tmp_path):
    store = Store(tmp_path)
    plugin = OtherPlugin(store)
    store.save_book("other", "path-word_1", plugin.detail("path-word_1"))
    with TestClient(create_app(store, {"other": plugin})) as client:
        use_id_rule(client)
        assert client.post("/api/library/sync/run").status_code == 202
        run = wait_for_sync(client)
        assert run["status"] == "completed" and run["downloaded"] == 9, run
        assert (library_root(client) / "other" / "path-word_1" / "1" / "00001.png").is_file()
        plugin.requests.clear()
        assert client.post("/api/library/sync/run").status_code == 202
        assert wait_for_sync(client)["skipped"] == 9 and not plugin.requests


def test_disabling_running_source_defers_its_books(tmp_path):
    store = Store(tmp_path)
    plugin = OtherPlugin(store)
    plugin.delay = .05
    original = plugin.stream

    @contextmanager
    def slow(method, url, **kwargs):
        time.sleep(.05)
        with original(method, url, **kwargs) as response:
            yield response
    plugin.client.stream = slow
    store.save_book("other", "1", plugin.detail("1"))
    with TestClient(create_app(store, {"other": plugin})) as client:
        use_id_rule(client)
        assert client.post("/api/library/sync/run").status_code == 202
        for _ in range(300):
            if client.app.state.library_images.child:
                break
            time.sleep(.01)
        assert client.put("/api/sources/other/state", json={"enabled": False}).status_code == 200
        run = wait_for_sync(client)
    assert run["status"] == "partial" and run["deferred_albums"] == 1, run
