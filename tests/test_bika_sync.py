import asyncio
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.errors import ComicApiError
from src.services.aggregator import AggregatorService
from src.services.bika_sync import BikaSync
from src.services.favorite_sync import BaseSyncSettings
from src.services.jm_sync import JmSync, SyncSettings
from src.services.local_images import safe_name
from src.storage import Store
from tests.fakes import DemoPlugin
from tests.test_jm_sync import FavoritePlugin

A, B = "5e7f2a9c1b3d4e6f8a0b2c4d", "5e7f2a9c1b3d4e6f8a0b2c4e"


class BikaFavoritePlugin(DemoPlugin):
    id = "bika"
    name = "哔咔测试图源"
    capabilities = ["search", "detail", "pages", "favorites", "login", "add_favorite"]

    def __init__(self, store):
        super().__init__(store)
        self.store.save_account("bika", {"account": "bika-account", "token": "bika-token"})
        self.requests, self.favorite_calls = [], []
        self.client.stream = self.stream

    def account_status(self):
        account = self.store.account("bika") or {}
        return {"authenticated": bool(account.get("token")), "configured": bool(account.get("account"))}

    @contextmanager
    def stream(self, method, url, **kwargs):
        self.requests.append(url)
        response = SimpleNamespace(status_code=200, content=self.picture)
        response.iter_content = lambda: iter([self.picture])
        yield response

    def favorites(self, page=1, sort="dd"):
        self.favorite_calls.append((page, sort))
        ids = [A, B]
        return {"items": [self.detail(ids[page - 1])] if page <= len(ids) else [], "total": len(ids),
                "page": page, "page_size": 1, "has_next": page < len(ids)}

    def detail(self, comic_id):
        return {"id": comic_id, "source": "bika", "title": f"哔咔漫画 {comic_id[-1]}", "author": "作者",
                "chapters": [{"id": str(i), "name": f"第{i}话", "order": i} for i in (1, 2)]}

    def pages(self, comic_id, chapter_id):
        return [f"https://storage.invalid/static/{comic_id}/{chapter_id}/{index}.png" for index in (1, 2)]


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "default-data"))
    monkeypatch.delenv("JM_DOWNLOAD_ROOTS", raising=False)
    monkeypatch.delenv("BIKA_DOWNLOAD_ROOTS", raising=False)
    monkeypatch.setattr("src.services.local_images.ensure_public_url", lambda url: None)


def make_bika_sync(tmp_path, **settings):
    store = Store(tmp_path)
    plugin = BikaFavoritePlugin(store)
    sync = BikaSync(store, AggregatorService(store, {"bika": plugin}))
    sync.save_settings(BaseSyncSettings(**{"directory_rule": "title", "retries": 0, **settings}))
    return sync, plugin


async def finish(sync, **kwargs):
    sync.launch(**kwargs)
    await sync.task
    return sync.current


def test_full_sync_scans_oldest_first_and_writes_hex_id_directories(tmp_path):
    asyncio.run(_full_sync(tmp_path))


async def _full_sync(tmp_path):
    sync, plugin = make_bika_sync(tmp_path)
    state = await finish(sync)
    assert state["status"] == "completed", state
    assert plugin.favorite_calls == [(1, "da"), (2, "da")]
    assert state["albums_total"] == 2 and state["downloaded"] == 8
    root = Path(sync.settings()["download_dir"])
    assert root == (tmp_path / "bika-favorites").resolve()
    assert (root / f"哔咔漫画 d [{A}]" / "001 - 第1话 [1]" / "00001.png").is_file()
    books = {book["id"]: book for book in sync.store.books()}
    assert set(books) == {A, B} and books[A]["library_category"] == "哔咔收藏"
    with sync.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM bika_sync_files").fetchone()[0] == 8
        assert db.execute("SELECT COUNT(*) FROM jm_sync_files").fetchone()[0] == 0
    plugin.requests.clear()
    state = await finish(sync, album_ids=[A])
    assert state["skipped"] == 4 and not state["downloaded"] and not plugin.requests


def test_local_check_matches_hex_id_directories_and_skips_downloads(tmp_path):
    asyncio.run(_local_check(tmp_path))


async def _local_check(tmp_path):
    sync, plugin = make_bika_sync(tmp_path, add_to_library=False)
    root = Path(sync.settings()["download_dir"])
    book = plugin.detail(A)
    for index, chapter in enumerate(book["chapters"], 1):
        target = root / f"{safe_name(book['title'])} [{A.upper()}]" / f"{index:03d} - {chapter['name']} [{chapter['id']}]"
        target.mkdir(parents=True)
        for page in (1, 2):
            (target / f"{page:05d}.png").write_bytes(plugin.picture)
    state = await finish(sync, check_local=True)
    assert state["local_albums"] == 1 and state["local_missing"] == 1 and not state["failed"], state
    with sync.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM bika_sync_local_files WHERE album_id=?", (A,)).fetchone()[0] == 4
    plugin.requests.clear()
    state = await finish(sync, album_ids=[A])
    assert state["skipped"] == 4 and not plugin.requests


def test_invalid_upstream_ids_fail_without_writing(tmp_path):
    asyncio.run(_invalid_ids(tmp_path))


async def _invalid_ids(tmp_path):
    sync, plugin = make_bika_sync(tmp_path)
    plugin.favorites = lambda page=1, sort="dd": {"items": [{"id": "../escape", "title": "x"}], "total": 1,
                                                 "page": 1, "page_size": 1, "has_next": False}
    state = await finish(sync)
    assert state["status"] == "failed" and "无效编号" in state["error"]
    assert not any(Path(sync.settings()["download_dir"]).iterdir())


def test_bika_settings_reject_jm_only_options(tmp_path):
    with pytest.raises(ValueError):
        BaseSyncSettings(folder_ids=["1"])
    with pytest.raises(ValueError):
        BaseSyncSettings(directory_rule="Bd_Aauthoroname_Pindextitle")
    sync, _ = make_bika_sync(tmp_path)
    rules = {item["value"] for item in sync.options()["directory_rules"]}
    assert "Bd_Aauthoroname_Pindextitle" not in rules and sync.options()["roots_env"] == "BIKA_DOWNLOAD_ROOTS"


def test_download_roots_follow_bika_environment(tmp_path, monkeypatch):
    extra = tmp_path / "bika-root"
    monkeypatch.setenv("BIKA_DOWNLOAD_ROOTS", str(extra))
    sync, _ = make_bika_sync(tmp_path)
    assert sync.paths.roots == [extra.resolve()]
    with pytest.raises(ComicApiError) as error:
        sync.paths.resolve(tmp_path / "bika-favorites")
    assert "BIKA_DOWNLOAD_ROOTS" in str(error.value)


@pytest.fixture
def client(tmp_path):
    from main import create_app
    sync, plugin = make_bika_sync(tmp_path)
    jm = FavoritePlugin(sync.store)
    with TestClient(create_app(sync.store, {"bika": plugin, "jm": jm})) as client:
        yield client


def wait(client, source):
    for _ in range(300):
        data = client.get(f"/api/{source}/sync").json()
        if not data["active"]:
            return data["history"][0]
        time.sleep(.01)
    pytest.fail("sync did not finish")


def test_bika_sync_api_validates_ids_and_runs_independently(client):
    settings = client.get("/api/bika/sync/settings").json()["settings"]
    assert "folder_ids" not in settings and not settings["enabled"]
    assert client.put("/api/bika/sync/settings", json=dict(settings, folder_ids=["1"])).status_code == 422
    assert client.put("/api/bika/sync/settings", json=dict(settings, download_dir="/etc")).status_code == 422
    assert client.post("/api/bika/sync/run", json={"album_ids": ["123"]}).status_code == 422
    assert client.post("/api/bika/sync/run", json={"album_ids": ["../" + A[3:]]}).status_code == 422
    assert client.post("/api/bika/sync/run", json={"album_ids": [A.upper()]}).status_code == 202
    run = wait(client, "bika")
    assert run["status"] == "completed" and run["downloaded"] == 4
    assert client.get("/api/jm/sync").json()["history"] == []
    assert client.post("/api/bika/sync/cancel").status_code == 409
    assert client.get("/api/bika/sync/directories").json()["path"].endswith("bika-favorites")
    assert client.post("/api/bika/sync/check-local").status_code == 202
    assert wait(client, "bika")["mode"] == "check_local"


def test_active_bika_sync_blocks_account_change_and_logout_disables_schedule(client):
    store = client.app.state.store
    settings = client.get("/api/bika/sync/settings").json()["settings"]
    assert client.put("/api/bika/sync/settings", json=dict(settings, enabled=True)).status_code == 200
    plugin = client.app.state.service.sources["bika"]
    plugin.stream_delay = .2
    original = plugin.stream

    @contextmanager
    def slow(method, url, **kwargs):
        time.sleep(plugin.stream_delay)
        with original(method, url, **kwargs) as response:
            yield response
    plugin.client.stream = slow
    sync = client.app.state.bika_sync
    for _ in range(300):
        if sync.active:
            break
        time.sleep(.01)
    assert sync.active
    assert client.delete("/api/sources/bika/account").status_code == 409
    assert client.post("/api/bika/sync/cancel").status_code == 200
    wait(client, "bika")
    plugin.logout = lambda: store.save_account("bika", {})
    assert client.delete("/api/sources/bika/account").status_code == 200
    assert store.settings("bika_sync")["enabled"] is False
    assert client.post("/api/bika/sync/run", json={}).status_code == 401


def test_jm_sync_keeps_folder_scopes(tmp_path):
    store = Store(tmp_path)
    sync = JmSync(store, AggregatorService(store, {"jm": FavoritePlugin(store)}))
    assert sync.scopes({"folder_ids": []}) == [{"folder_id": "0", "order": "mr"}]
    assert sync.scopes({"folder_ids": ["7", "9"]})[1] == {"folder_id": "9", "order": "mr"}
    assert SyncSettings(folder_ids=["7", "7"]).folder_ids == ["7"]
