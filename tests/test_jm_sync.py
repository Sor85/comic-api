import asyncio
import io
import json
import re
import threading
import time
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.clients.jm import JmClient
from src.errors import ComicApiError
from src.services.aggregator import AggregatorService
from src.services.images import descramble_jm_image
from src.services.image_sync import ImageSyncSettings
from src.services.library_image_sync import LibraryImageSync
from src.services.local_images import DownloadPaths, encode_image, receive_image, safe_name
from src.sources.jm import JmPlugin
from src.storage import Store
from tests.fakes import DemoPlugin


class FavoritePlugin(DemoPlugin):
    id = "jm"
    name = "禁漫测试图源"
    capabilities = ["search", "detail", "pages", "favorites", "login"]

    def __init__(self, store):
        super().__init__(store)
        self.store.save_account("jm", {"account": "test-account", "token": "test-session"})
        self.requests = []
        self.favorite_calls = []
        self.page_count = 2
        self.broken = set()
        self.extra_chapter = False
        self.client.stream = self.stream

    @contextmanager
    def stream(self, *args, **kwargs):
        response = self.request(*args, **kwargs)
        response.iter_content = lambda: iter([response.content])
        yield response

    def favorites(self, page=1, folder_id="0", order="mr"):
        self.favorite_calls.append((page, folder_id, order))
        ids = ["101", "102"] if folder_id == "0" else ["101"]
        return {"items": [self.detail(ids[page-1])] if page <= len(ids) else [],
                "folders": [{"id": "0", "name": "全部收藏"}, {"id": "7", "name": "测试收藏"}],
                "total": len(ids), "has_next": page < len(ids), "page": page, "page_size": 1}

    def detail(self, comic_id):
        return {"id": comic_id, "source": "jm", "title": f"漫画 {comic_id}", "author": "作者 / name",
                "chapters": [{"id": f"{comic_id}{i}", "name": f"第 {i} 章"} for i in range(1, 4 if self.extra_chapter else 3)]}

    def pages(self, comic_id, chapter_id):
        return [f"https://images.invalid/{chapter_id}/{index}.png" for index in range(1, self.page_count+1)]

    def request(self, method, url, **kwargs):
        self.requests.append((url, kwargs))
        time.sleep(self.delay)
        if any(value in url for value in self.broken):
            raise ComicApiError("模拟下载失败", 502, "upstream_error", "jm")
        callback = kwargs.get("content_callback")
        if callback:
            if callback(self.picture) != len(self.picture):
                raise ComicApiError("接收中止", 502, "upstream_network", "jm")
            return SimpleNamespace(status_code=200, content=b"")
        return SimpleNamespace(status_code=200, content=self.picture)


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "default-data"))
    monkeypatch.delenv("LIBRARY_DOWNLOAD_ROOTS", raising=False)
    monkeypatch.setattr("src.services.local_images.ensure_public_url", lambda url: None)


def make_sync(tmp_path, rule="id", books=("101", "102")):
    """Library sync over a JM test source whose ``books`` are already in the library."""
    store = Store(tmp_path)
    plugin = FavoritePlugin(store)
    library = LibraryImageSync(store, AggregatorService(store, {"jm": plugin}), {})
    library.save_settings(ImageSyncSettings(directory_rule=rule, retries=0))
    for comic_id in books:
        store.save_book("jm", comic_id, plugin.detail(comic_id))
    return library, plugin


def save(library, **values):
    library.save_settings(ImageSyncSettings(**dict(library.settings(), **values)))


def jm_root(library):
    return library.worker("jm").root()


async def finish(library, **kwargs):
    library.launch(**kwargs)
    await library.task
    return library.current


def test_jm_favorite_protocol_and_fixed_pagination(tmp_path, monkeypatch):
    client = JmClient(Store(tmp_path))
    calls = []
    payload = {"list": [{"id": "123", "name": "Title", "image": "", "author": "Author"}],
               "folder_list": [{"FID": "9", "name": "Folder"}], "total": "21", "count": 1}
    monkeypatch.setattr(client, "jm_request", lambda *args, **kwargs: calls.append((args, kwargs)) or payload)
    data = client.favorites(page=1, folder_id="9", order="mp")
    assert calls[0] == (("/favorite",), {"params": {"page": "1", "folder_id": "9", "o": "mp"}, "_require_auth": True})
    assert data["folders"] == [{"id": "0", "name": "全部收藏"}, {"id": "9", "name": "Folder"}]
    assert data["total"] == 21 and data["page_size"] == 20 and data["has_next"]
    assert data["items"][0]["source"] == "jm"
    assert not client.favorites(2)["has_next"]
    with pytest.raises(ComicApiError, match="收藏夹已不存在"):
        client.favorites(folder_id="8")


@pytest.mark.parametrize("payload", [{}, {"list": [], "folder_list": [], "total": 10},
    {"list": [], "folder_list": [], "total": "bad"}, {"list": [None], "folder_list": [], "total": 1}])
def test_malformed_favorites_are_not_empty_success(tmp_path, monkeypatch, payload):
    client = JmClient(Store(tmp_path))
    monkeypatch.setattr(client, "jm_request", lambda *a, **k: payload)
    with pytest.raises(ComicApiError) as error:
        client.favorites()
    assert error.value.code == "invalid_response"


def test_favorites_require_account_before_network(tmp_path, monkeypatch):
    client = JmClient(Store(tmp_path))
    monkeypatch.setattr(client, "request", lambda *a, **k: pytest.fail("unexpected network"))
    with pytest.raises(ComicApiError) as error:
        client.favorites()
    assert error.value.code == "login_required"


def response(value, status=200):
    return SimpleNamespace(content=json.dumps(value).encode(), status_code=status, cookies={"session": "cookie"})


def test_cookie_session_login_persists_encrypted_and_logout_clears(tmp_path, monkeypatch):
    store = Store(tmp_path)
    client = JmClient(store)
    client.host_resolved = True
    monkeypatch.setattr(client, "request", lambda *a, **k: response({"s": "avs-private", "uid": "1"}))
    client.login("alice", "private-password")
    assert store.account("jm")["avs"] == "avs-private"
    replacement = JmPlugin(store)
    assert replacement.account_status()["authenticated"]
    assert replacement.client.auth_cookies["AVS"] == "avs-private"
    with store.connect() as db:
        assert "avs-private" not in db.execute("SELECT secret FROM accounts WHERE source='jm'").fetchone()[0]
    replacement.logout()
    assert not JmPlugin(store).account_status()["authenticated"]
    assert store.account("jm") == {}


def test_favorite_expired_session_reauthenticates_without_anonymous_fallback(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.save_account("jm", {"account": "a", "password": "p", "avs": "old"})
    client = JmClient(store)
    client.host_resolved = True
    replies = iter([response({}, 401), response({"s": "new"}), response({"list": [], "folder_list": [], "total": 0})])
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return next(replies)
    monkeypatch.setattr(client, "request", request)
    assert client.favorites()["items"] == []
    assert calls[0][2]["cookies"]["AVS"] == "old"
    assert calls[1][0] == "POST" and calls[1][1].endswith("/login")
    assert calls[2][2]["cookies"]["AVS"] == "new"
    assert store.account("jm")["avs"] == "new"


def test_login_can_fall_back_to_cookie_api_signature(tmp_path, monkeypatch):
    from src.config import Config
    store = Store(tmp_path)
    client = JmClient(store)
    client.host_resolved = True
    calls = []

    def request(method, url, **kwargs):
        calls.append(kwargs)
        timestamp = kwargs["headers"]["tokenparam"].split(",")[0]
        if kwargs["headers"]["token"] != client.md5_hex(timestamp + Config.JM_SECRET):
            return response({"code": 403})
        return response({"s": "cookie-session", "uid": "1"})

    monkeypatch.setattr(client, "request", request)
    client.login("a", "p")
    assert len(calls) == 2
    assert client.cookie_api and store.account("jm")["cookie_api"]
    restored = JmClient(store)
    assert restored.get_api_headers(123)["token"] == restored.md5_hex(f"123{Config.JM_SECRET}")


def test_failed_login_keeps_previous_session_and_signature(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.save_account("jm", {"account": "original", "password": "p", "token": "valid"})
    client = JmClient(store)
    client.host_resolved = True
    monkeypatch.setattr(client, "request", lambda *a, **k: response({"code": 403}))
    with pytest.raises(ComicApiError):
        client.login("different", "incorrect")
    assert client.jwt_token == "valid" and not client.cookie_api
    assert store.account("jm")["account"] == "original"


def test_api_envelope_error_not_a_valid_empty_collection(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.save_account("jm", {"token": "test"})
    client = JmClient(store)
    client.host_resolved = True
    monkeypatch.setattr(client, "request", lambda *a, **k: response({"code": 500, "data": None, "errorMsg": "bad"}))
    with pytest.raises(ComicApiError) as error:
        client.favorites()
    assert error.value.code == "upstream_error"


def test_sync_incremental_repair_and_new_chapters(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        library.store.save_book("jm", "101", plugin.detail("101"), "手动分类")
        state = await finish(library)
        assert state["status"] == "completed", state
        assert state["downloaded"] == 8
        assert next(b for b in library.store.books() if b["id"] == "101")["library_category"] == "手动分类"
        before = len(plugin.requests)
        state = await finish(library)
        assert state["skipped"] == 8 and state["skipped_albums"] == 2 and len(plugin.requests) == before
        path = jm_root(library) / "101" / "1011" / "00001.png"
        path.write_bytes(b"corrupted")
        plugin.extra_chapter = True
        state = await finish(library)
        assert state["downloaded"] == 5 and state["skipped"] == 7
        with Image.open(path) as image:
            assert image.format == "PNG"
    asyncio.run(scenario())


def test_category_scope_and_selected_items(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        library.store.save_book("jm", "101", plugin.detail("101"), "精选")
        save(library, categories=["精选"])
        assert library.options()["scope_books"] == 1
        state = await finish(library)
        assert state["albums_total"] == 1 and state["downloaded"] == 4
        assert not (jm_root(library) / "102").exists()
        save(library, categories=[""])
        state = await finish(library)
        assert state["albums_total"] == 1 and state["downloaded"] == 4 and (jm_root(library) / "102").is_dir()
        state = await finish(library, items=[("jm", "102"), ("jm", "102")])
        assert state["albums_total"] == 1 and state["selected"] and state["skipped"] == 4
    asyncio.run(scenario())


def test_selected_favorite_outside_library_is_added_then_synced(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path, books=())
        state = await finish(library, items=[("jm", "103")])
        assert state["status"] == "completed", state
        assert state["added_books"] == 1 and state["downloaded"] == 4
        book = next(b for b in library.store.books() if b["id"] == "103")
        assert book["library_categories"] == ["JM 收藏"] and book["library_category"] == ""
        assert library.worker("jm").local_status(["103"])["items"]["103"]["status"] == "downloaded"
        # Check and repair runs never add books.
        state = await finish(library, mode="check_local", items=[("jm", "104")])
        assert state["failed"] == 1 and "不在书库中" in state["errors"][0]["message"]
        assert not any(b["id"] == "104" for b in library.store.books())
    asyncio.run(scenario())


def test_partial_failure_retry_and_conflict_preserves_external_file(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        plugin.broken = {"1011/1"}
        state = await finish(library, items=[("jm", "101")])
        assert state["status"] == "partial" and state["failed"] == 1
        assert state["downloaded"] == 3
        target = jm_root(library) / "101" / "1011" / "00001.png"
        target.write_bytes(b"user-owned")
        plugin.broken.clear()
        state = await finish(library, items=[("jm", "101")])
        assert state["status"] == "partial"
        assert target.read_bytes() == b"user-owned"
        target.unlink()
        state = await finish(library, items=[("jm", "101")])
        assert state["status"] == "completed" and state["downloaded"] == 1
        assert state["skipped"] == 3
    asyncio.run(scenario())


def test_fixed_format_conflict_is_refused_before_downloading(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        save(library, image_format="png")
        target = jm_root(library) / "101" / "1011" / "00001.png"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"user-owned")
        state = await finish(library, items=[("jm", "101")])
        assert state["status"] == "partial" and state["failed"] == 1
        assert "未覆盖" in state["errors"][0]["message"]
        assert not any("1011/1.png" in url for url, _ in plugin.requests)
        assert target.read_bytes() == b"user-owned"
    asyncio.run(scenario())


def test_format_and_quality_change_replaces_only_tracked_files(tmp_path):
    async def scenario():
        library, _ = make_sync(tmp_path)
        for image_format, quality in [("png", 90), ("jpeg", 85), ("webp", 75), ("jpeg", 95), ("png", 90)]:
            save(library, image_format=image_format, quality=quality)
            state = await finish(library, items=[("jm", "101")])
            assert state["status"] == "completed", state
        directory = jm_root(library) / "101" / "1011"
        for extension, expected in [("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP")]:
            with Image.open(directory / f"00001.{extension}") as image:
                assert image.format == expected
    asyncio.run(scenario())


def test_active_cancel_and_restart_preserve_checkpoints(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        plugin.delay = .06
        library.launch(items=[("jm", "101")])
        while library.status()["current"]["downloaded"] < 1:
            await asyncio.sleep(.01)
        with pytest.raises(ComicApiError):
            library.launch()
        with pytest.raises(ComicApiError):
            library.save_settings(ImageSyncSettings())
        library.cancel()
        await library.task
        assert library.current["status"] == "cancelled" and library.current["downloaded"] >= 1
        saved = library.current["downloaded"]
        library.current["status"] = "running"
        library._persist(library.current)
        restarted = LibraryImageSync(Store(tmp_path), library.service, {})
        await restarted.start()
        assert restarted.status()["history"][0]["status"] == "interrupted"
        restarted.launch(items=[("jm", "101")])
        await restarted.task
        assert restarted.current["skipped"] == saved
        assert restarted.current["status"] == "completed"
        await restarted.stop()
    asyncio.run(scenario())


def test_automatic_schedule_runs_full_scope(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        await library.start()
        save(library, enabled=True)
        for _ in range(200):
            if library.task and library.task.done():
                break
            await asyncio.sleep(.02)
        assert library.current["automatic"] and library.current["status"] == "completed"
        assert library.current["downloaded"] == 8
        assert library.next_run_at > time.time()
        await library.stop()
    asyncio.run(scenario())


def test_directory_bounds_normalization_and_symlinks(tmp_path, monkeypatch):
    paths = DownloadPaths(Store(tmp_path / "data"), "LIBRARY_DOWNLOAD_ROOTS", "library")
    root = paths.roots[0]
    assert paths.resolve(str(root).replace("/", "\\")) == root
    paths.prepare(root / "new")
    assert paths.browse()["directories"] == [{"name": "new", "path": str(root / "new")}]
    for path in (tmp_path, root / ".." / "escape", Path("C:/Users/downloads")):
        with pytest.raises(ComicApiError):
            paths.resolve(path)
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ComicApiError):
        paths.resolve(root / "link" / "outside")
    assert "link" not in [item["name"] for item in paths.browse()["directories"]]
    extra = tmp_path / "volume"
    monkeypatch.setenv("LIBRARY_DOWNLOAD_ROOTS", str(extra))
    assert DownloadPaths(Store(tmp_path / "other-data"), "LIBRARY_DOWNLOAD_ROOTS", "library").roots == [extra]
    assert safe_name("CON") == "_CON"
    assert "/" not in safe_name("../../name:bad")


def test_container_mapped_directories_become_roots_before_data_directory(tmp_path, monkeypatch):
    data = tmp_path / "app" / "data"
    comics = tmp_path / "mnt" / "hdd" / "漫画 库"
    nested = data / "cache"
    for path in (data, comics, nested):
        path.mkdir(parents=True)
    (tmp_path / "hosts").write_text("")
    escaped = str(comics).replace(" ", "\\040")
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("\n".join([
        "1 0 0:1 / / rw - overlay overlay rw",
        f"2 1 0:2 / {data} rw - ext4 /dev/sda1 rw",
        f"3 1 0:3 /comics {escaped} rw shared:1 - fuse.shfs shfs rw",
        f"4 1 0:4 / {nested} rw - ext4 /dev/sda1 rw",
        "5 1 0:5 / /proc rw - proc proc rw",
        f"6 1 0:6 / {tmp_path / 'hosts'} rw - ext4 /dev/sda1 rw",
        "7 1 0:7 / /etc/hostname rw - ext4 /dev/sda1 rw",
    ]), encoding="utf-8")
    marker = tmp_path / ".dockerenv"
    monkeypatch.setattr("src.services.local_images.MOUNTINFO", mountinfo)
    monkeypatch.setattr("src.services.local_images.CONTAINER_MARKERS", (marker,))
    store = Store(data)
    # Outside a container the host's own mounts are never offered.
    assert DownloadPaths(store, "LIBRARY_DOWNLOAD_ROOTS", "library").roots == [(data / "library").resolve()]
    marker.write_text("")
    paths = DownloadPaths(store, "LIBRARY_DOWNLOAD_ROOTS", "library")
    assert paths.roots == [comics, (data / "library").resolve()]
    assert paths.resolve(comics / "jm") == comics.resolve() / "jm"
    monkeypatch.setenv("LIBRARY_DOWNLOAD_ROOTS", str(tmp_path / "only"))
    assert DownloadPaths(store, "LIBRARY_DOWNLOAD_ROOTS", "library").roots == [(tmp_path / "only").resolve()]


@pytest.mark.parametrize("rule", ["id", "title", "author"])
def test_directory_rules_are_contained_and_id_stable(tmp_path, rule):
    library, plugin = make_sync(tmp_path)
    worker = library.worker("jm")
    book = plugin.detail("101")
    book["title"] = "../../漫画\\标题"
    book["author"] = "CON"
    chapter = {"id": "1011", "name": "../../章节"}
    config = dict(worker.settings(), directory_rule=rule)
    path = worker.paths.chapter(config, book, chapter, 1)
    assert path.is_relative_to(Path(config["download_dir"]))
    assert "101" in str(path) and "1011" in str(path)
    assert len(path.relative_to(config["download_dir"]).parts) == (3 if rule == "author" else 2)


def test_settings_reject_removed_jmcomic_rule():
    with pytest.raises(ValueError):
        ImageSyncSettings(directory_rule="Bd_Aauthoroname_Pindextitle")
    with pytest.raises(ValueError):
        ImageSyncSettings(folder_ids=["1"])
    assert ImageSyncSettings(categories=["A", "A", ""]).categories == ["A", ""]


def test_images_alpha_conversion_and_animated_original():
    output = io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(output, "PNG")
    data, extension = encode_image(output.getvalue(), "jpeg", 90)
    with Image.open(io.BytesIO(data)) as image:
        assert extension == "jpg" and image.mode == "RGB" and image.getpixel((0, 0)) == (255, 255, 255)
    gif = io.BytesIO()
    first, second = Image.new("RGB", (8, 8), "red"), Image.new("RGB", (8, 8), "blue")
    first.save(gif, "GIF", save_all=True, append_images=[second], duration=100, loop=0)
    decoded = descramble_jm_image(gif.getvalue(), "500000", "https://example.org/1.gif")
    assert decoded == gif.getvalue()
    assert encode_image(decoded, "original", 90) == (decoded, "gif")
    converted, _ = encode_image(decoded, "png", 90)
    with Image.open(io.BytesIO(converted)) as image:
        assert image.format == "PNG"


def test_receive_limit_reaches_transport_and_bounds_short_transfers(tmp_path):
    _, plugin = make_sync(tmp_path)
    # Make a small image take at least 0.5 seconds even when the fake transport delivers instantly.
    plugin.picture = plugin.picture + b"\0" * 2048
    config = ImageSyncSettings(speed_limit_kbps=4, retries=0).model_dump()
    start = time.monotonic()
    data, extension, received = receive_image(plugin, "https://images.invalid/1.png", "1", config, threading.Event())
    assert time.monotonic() - start >= received / 4096 - .02
    assert plugin.requests[0][1]["max_recv_speed"] == 4096
    assert plugin.requests[0][1]["allow_redirects"] is False
    assert extension == "png" and data


def test_real_curl_stream_transport_and_rate_limit(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from src.clients.base import BaseClient
    _, plugin = make_sync(tmp_path)
    payload = plugin.picture + b"\0" * 4096
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    plugin.client = BaseClient()
    try:
        url = f"http://127.0.0.1:{server.server_port}/image.png"
        started = time.monotonic()
        data, extension, received = receive_image(plugin, url, "1", ImageSyncSettings(speed_limit_kbps=8, retries=0).model_dump(), threading.Event())
        assert data == payload and extension == "png" and received == len(payload)
        assert time.monotonic() - started >= len(payload) / 8192 - .03
        assert seen[0]["Referer"] == f"http://127.0.0.1:{server.server_port}/"
    finally:
        plugin.client.session.close()
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_stream_permit_is_held_until_response_closes(monkeypatch):
    from src.clients.base import BaseClient
    client = BaseClient()
    client._request_slots = threading.BoundedSemaphore(1)
    closed = []
    monkeypatch.setattr(client, "_send", lambda *a, **k: SimpleNamespace(close=lambda: closed.append(True)))
    with client.stream("GET", "https://example.invalid"):
        assert not client._request_slots.acquire(blocking=False)
    assert closed == [True] and client._request_slots.acquire(blocking=False)
    client._request_slots.release()


@pytest.fixture
def jm_client(tmp_path):
    from main import create_app
    library, plugin = make_sync(tmp_path)
    with TestClient(create_app(library.store, {"jm": plugin})) as client:
        yield client


def wait_for_run(client):
    for _ in range(300):
        data = client.get("/api/library/sync").json()
        if not data["active"]:
            return data["history"][0]
        time.sleep(.01)
    pytest.fail("sync did not finish")


def test_sync_api_and_static_ui(jm_client):
    client = jm_client
    assert client.get("/api/jm/favorites").json()["items"]
    assert client.get("/api/jm/favorites?page=0").status_code == 422
    assert client.get("/api/jm/favorites?order=invalid").status_code == 422
    settings = client.get("/api/library/sync/settings").json()["settings"]
    assert not settings["enabled"]
    assert client.put("/api/library/sync/settings", json=dict(settings, speed_limit_kbps=-1)).status_code == 422
    assert client.put("/api/library/sync/settings", json=dict(settings, download_dir="/etc")).status_code == 422
    assert client.put("/api/library/sync/settings", json=dict(settings, directory_rule="Bd_Aauthoroname_Pindextitle")).status_code == 422
    assert client.get("/api/library/sync/directories?path=/etc").status_code == 422
    assert client.post("/api/library/sync/run", json={"items": [{"source": "jm", "id": "../1"}]}).status_code == 422
    assert client.post("/api/library/sync/run", json={"items": []}).status_code == 422
    assert client.post("/api/library/sync/run", json={"items": [{"source": "jm", "id": "101"}]}).status_code == 202
    assert wait_for_run(client)["status"] == "completed"
    assert client.get("/api/library/sync/local-status?source=jm&ids=101").json()["items"]["101"]["status"] == "downloaded"
    assert client.post("/api/library/sync/cancel").status_code == 409
    for removed in ("/api/jm/sync", "/api/jm/sync/settings"):
        assert client.get(removed).status_code != 200
    assert client.get("/static/library-sync.js?v=test").status_code == 200
    html = client.get("/").text
    assert 'id="jm-favorites"' in html and '/static/library-sync.js?v=' in html
    assert 'id="jm-sync-open"' not in html and "Bd_Aauthoroname_Pindextitle" not in html
    assert client.delete("/api/sources/jm/account").status_code == 200
    assert client.get("/api/jm/favorites").status_code == 401


def test_directory_switch_warning_is_visible_before_changing_settings(jm_client):
    html = jm_client.get("/").text
    start = html.index('<div id="fav-sync-switch-note"')
    notice = html[start:html.index('<section', start)]
    assert 'role="note"' in notice and 'aria-labelledby="fav-sync-switch-title"' in notice
    assert "hidden" not in notice.split(">", 1)[0]
    assert "保存即同步一次" in notice and "旧目录未登记时会计为冲突并重复下载" in notice
    steps = re.findall(r"<li>.*?<span>([^<]+)</span></li>", notice)
    assert steps == ["关闭定时同步", "保存新规则", "检查本地漫画", "重新开启同步"]
    assert "每次保存设置都会立即同步一次" in notice and "不会等待下一个周期" in notice
    assert "包括仅修改目录规则或下载目录" in notice
    assert "未登记的原文件不会被覆盖" in notice and "消耗流量和时间" in notice
    assert "运行中的任务完成或停止后才能修改设置" in notice
    assert start < html.index('<select name="directory_rule">')


def test_active_sync_blocks_login_and_logout(jm_client):
    client = jm_client
    client.app.state.service.sources["jm"].delay = .2
    assert client.post("/api/library/sync/run", json={}).status_code == 202
    for _ in range(300):
        if client.app.state.library_images.source_active("jm"):
            break
        time.sleep(.01)
    assert client.delete("/api/sources/jm/account").status_code == 409
    assert client.post("/api/sources/jm/login", json={"account": "new", "password": "secret"}).status_code == 409
    assert client.post("/api/library/sync/cancel").status_code == 200
    assert wait_for_run(client)["status"] == "cancelled"
