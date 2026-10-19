import asyncio
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.services.aggregator import AggregatorService
from src.services.image_sync import ImageSyncSettings
from src.services.library_image_sync import LibraryImageSync
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
    monkeypatch.delenv("LIBRARY_DOWNLOAD_ROOTS", raising=False)
    monkeypatch.setattr("src.services.local_images.ensure_public_url", lambda url: None)


def make_bika_sync(tmp_path, books=(A, B), **settings):
    store = Store(tmp_path)
    plugin = BikaFavoritePlugin(store)
    library = LibraryImageSync(store, AggregatorService(store, {"bika": plugin}), {})
    library.save_settings(ImageSyncSettings(**{"directory_rule": "title", "retries": 0, **settings}))
    for comic_id in books:
        store.save_book("bika", comic_id, plugin.detail(comic_id))
    return library, plugin


def bika_root(library):
    return library.worker("bika").root()


async def finish(library, **kwargs):
    library.launch(**kwargs)
    await library.task
    return library.current


def test_full_sync_writes_hex_id_directories_under_source_folder(tmp_path):
    asyncio.run(_full_sync(tmp_path))


async def _full_sync(tmp_path):
    library, plugin = make_bika_sync(tmp_path)
    state = await finish(library)
    assert state["status"] == "completed", state
    assert state["albums_total"] == 2 and state["downloaded"] == 8
    root = bika_root(library)
    assert root == (tmp_path / "library" / "bika").resolve()
    assert (root / f"哔咔漫画 d [{A}]" / "001 - 第1话 [1]" / "00001.png").is_file()
    with library.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM library_bika_sync_files").fetchone()[0] == 8
        assert db.execute("SELECT COUNT(*) FROM bika_sync_files").fetchone()[0] == 0
    plugin.requests.clear()
    state = await finish(library, items=[("bika", A.upper())])
    assert state["skipped"] == 4 and not state["downloaded"] and not plugin.requests


def test_local_check_matches_hex_id_directories_and_skips_downloads(tmp_path):
    asyncio.run(_local_check(tmp_path))


async def _local_check(tmp_path):
    library, plugin = make_bika_sync(tmp_path)
    root = bika_root(library)
    book = plugin.detail(A)
    for index, chapter in enumerate(book["chapters"], 1):
        target = root / f"{safe_name(book['title'])} [{A.upper()}]" / f"{index:03d} - {chapter['name']} [{chapter['id']}]"
        target.mkdir(parents=True)
        for page in (1, 2):
            (target / f"{page:05d}.png").write_bytes(plugin.picture)
    state = await finish(library, mode="check_local")
    assert state["local_albums"] == 1 and state["local_missing"] == 1 and not state["failed"], state
    with library.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM library_bika_sync_local_files WHERE album_id=?", (A,)).fetchone()[0] == 4
    plugin.requests.clear()
    state = await finish(library, items=[("bika", A)])
    assert state["skipped"] == 4 and not plugin.requests


def test_invalid_library_ids_fail_without_writing(tmp_path):
    asyncio.run(_invalid_ids(tmp_path))


async def _invalid_ids(tmp_path):
    library, plugin = make_bika_sync(tmp_path, books=())
    library.store.save_book("bika", "../escape", {"title": "x", "chapters": []})
    state = await finish(library)
    assert state["status"] == "partial" and state["failed"] == 1 and "编号无效" in state["errors"][0]["message"]
    assert not any(bika_root(library).parent.iterdir()) and not plugin.requests


@pytest.fixture
def client(tmp_path):
    from main import create_app
    library, plugin = make_bika_sync(tmp_path)
    jm = FavoritePlugin(library.store)
    with TestClient(create_app(library.store, {"bika": plugin, "jm": jm})) as client:
        yield client


def wait(client):
    for _ in range(300):
        data = client.get("/api/library/sync").json()
        if not data["active"]:
            return data["history"][0]
        time.sleep(.01)
    pytest.fail("sync did not finish")


def item(comic_id):
    return {"items": [{"source": "bika", "id": comic_id}]}


def test_library_sync_api_validates_bika_ids(client):
    assert client.post("/api/library/sync/run", json=item("123")).status_code == 422
    assert client.post("/api/library/sync/run", json=item("../" + A[3:])).status_code == 422
    assert client.post("/api/library/sync/run", json=item(A.upper())).status_code == 202
    run = wait(client)
    assert run["status"] == "completed" and run["downloaded"] == 4
    assert client.get(f"/api/library/sync/local-status?source=bika&ids={A.upper()}").json()["items"][A]["status"] == "downloaded"
    assert client.get("/api/library/sync/directories").json()["path"].endswith("library")
    assert client.post("/api/library/sync/check-local").status_code == 202
    assert wait(client)["mode"] == "check_local"


def test_active_bika_worker_blocks_account_change(client):
    store = client.app.state.store
    plugin = client.app.state.service.sources["bika"]
    original = plugin.stream

    @contextmanager
    def slow(method, url, **kwargs):
        time.sleep(.2)
        with original(method, url, **kwargs) as response:
            yield response
    plugin.client.stream = slow
    assert client.post("/api/library/sync/run", json=item(A)).status_code == 202
    library = client.app.state.library_images
    for _ in range(300):
        if library.source_active("bika"):
            break
        time.sleep(.01)
    assert library.source_active("bika")
    assert client.delete("/api/sources/bika/account").status_code == 409
    assert client.post("/api/library/sync/cancel").status_code == 200
    wait(client)
    plugin.logout = lambda: store.save_account("bika", {})
    assert client.delete("/api/sources/bika/account").status_code == 200


def test_repair_keeps_source_named_pages_and_downloads_gaps_with_source_names(tmp_path):
    asyncio.run(_repair_source_names(tmp_path))


async def _repair_source_names(tmp_path):
    library, plugin = make_bika_sync(tmp_path, books=(A,))
    plugin.pages = lambda comic_id, chapter_id: [f"https://storage.invalid/static/{comic_id}/{name}-{chapter_id}.png" for name in ("first", "second")]
    root = bika_root(library)
    book = plugin.detail(A)
    for index, chapter in enumerate(book["chapters"], 1):
        target = root / f"{safe_name(book['title'])} [{A}]" / f"{index:03d} - {chapter['name']} [{chapter['id']}]"
        target.mkdir(parents=True)
        for name in ("first", "second"):
            (target / f"{name}-{chapter['id']}.png").write_bytes(plugin.picture)
    gap = target / "second-2.png"
    gap.unlink()
    state = await finish(library, mode="check_local")
    assert state["failed"] == 1, state
    state = await finish(library, mode="repair")
    assert state["status"] == "completed" and state["downloaded"] == 1 and not state["moved_images"], state
    assert gap.is_file() and sorted(p.name for p in target.iterdir()) == ["first-2.png", "second-2.png"]
    assert library.local_status("bika", [A])["items"][A]["status"] == "downloaded"
