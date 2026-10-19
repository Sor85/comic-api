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
from src.services.jm_sync import JmSync, SyncSettings
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

    def chapter_detail(self, comic_id, chapter_id):
        for index, chapter in enumerate(self.detail(comic_id)["chapters"], 1):
            if chapter["id"] == chapter_id:
                return dict(chapter, sort=chapter.get("sort", index), series_id=comic_id,
                            images=self.pages(comic_id, chapter_id))
        raise ComicApiError("测试章节不存在", 404, "not_found", "jm")

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
    monkeypatch.delenv("JM_DOWNLOAD_ROOTS", raising=False)
    monkeypatch.setattr("src.services.local_images.ensure_public_url", lambda url: None)


def make_sync(tmp_path):
    store = Store(tmp_path)
    plugin = FavoritePlugin(store)
    sync = JmSync(store, AggregatorService(store, {"jm": plugin}))
    sync.save_settings(SyncSettings(directory_rule="id", retries=0))
    return sync, plugin


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


def test_sync_pagination_incremental_repair_and_new_chapters(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        sync.store.save_book("jm", "101", plugin.detail("101"), "手动分类")
        sync.launch()
        await sync.task
        assert sync.current["status"] == "completed", sync.current
        assert sync.current["downloaded"] == 8
        assert plugin.favorite_calls == [(1, "0", "mr"), (2, "0", "mr")]
        assert next(b for b in sync.store.books() if b["id"] == "101")["library_category"] == "手动分类"
        before = len(plugin.requests)
        sync.launch()
        await sync.task
        assert sync.current["skipped"] == 8 and len(plugin.requests) == before
        path = Path(sync.settings()["download_dir"]) / "101" / "1011" / "00001.png"
        path.write_bytes(b"corrupted")
        plugin.extra_chapter = True
        sync.launch()
        await sync.task
        assert sync.current["downloaded"] == 5 and sync.current["skipped"] == 7
        with Image.open(path) as image:
            assert image.format == "PNG"
    asyncio.run(scenario())


def test_folder_scope_and_selected_album_sync(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        config = SyncSettings(**dict(sync.settings(), folder_ids=["7"]))
        sync.save_settings(config)
        sync.launch()
        await sync.task
        assert sync.current["albums_total"] == 1
        assert plugin.favorite_calls == [(1, "7", "mr")]
        sync.launch(["102", "102"])
        await sync.task
        assert sync.current["albums_total"] == 1 and sync.current["downloaded"] == 4
    asyncio.run(scenario())


def test_partial_failure_retry_and_conflict_preserves_external_file(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        plugin.broken = {"1011/1"}
        sync.launch(["101"])
        await sync.task
        assert sync.current["status"] == "partial" and sync.current["failed"] == 1
        assert sync.current["downloaded"] == 3
        target = Path(sync.settings()["download_dir"]) / "101" / "1011" / "00001.png"
        target.write_bytes(b"user-owned")
        plugin.broken.clear()
        sync.launch(["101"])
        await sync.task
        assert sync.current["status"] == "partial"
        assert target.read_bytes() == b"user-owned"
        target.unlink()
        sync.launch(["101"])
        await sync.task
        assert sync.current["status"] == "completed" and sync.current["downloaded"] == 1
        assert sync.current["skipped"] == 3
    asyncio.run(scenario())


def test_format_and_quality_change_replaces_only_tracked_files(tmp_path):
    async def scenario():
        sync, _ = make_sync(tmp_path)
        for image_format, quality in [("png", 90), ("jpeg", 85), ("webp", 75), ("jpeg", 95), ("png", 90)]:
            sync.save_settings(SyncSettings(**dict(sync.settings(), image_format=image_format, quality=quality)))
            sync.launch(["101"])
            await sync.task
            assert sync.current["status"] == "completed", sync.current
        directory = Path(sync.settings()["download_dir"]) / "101" / "1011"
        for extension, expected in [("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP")]:
            with Image.open(directory / f"00001.{extension}") as image:
                assert image.format == expected
    asyncio.run(scenario())


def test_active_cancel_and_restart_preserve_checkpoints(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        plugin.delay = .06
        sync.launch(["101"])
        while sync.current["downloaded"] < 1:
            await asyncio.sleep(.01)
        with pytest.raises(ComicApiError):
            sync.launch()
        with pytest.raises(ComicApiError):
            sync.save_settings(SyncSettings())
        sync.cancel()
        await sync.task
        assert sync.current["status"] == "cancelled" and sync.current["downloaded"] >= 1
        saved = sync.current["downloaded"]
        sync.current["status"] = "running"
        sync._persist(sync.current)
        restarted = JmSync(Store(tmp_path), sync.service)
        await restarted.start()
        assert restarted.history()[0]["status"] == "interrupted"
        restarted.launch(["101"])
        await restarted.task
        assert restarted.current["skipped"] == saved
        assert restarted.current["status"] == "completed"
        await restarted.stop()
    asyncio.run(scenario())


def test_repeated_pagination_stops_instead_of_looping(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        plugin.favorites = lambda **kwargs: {"items": [{"id": "101"}], "has_next": True}
        sync.launch()
        await sync.task
        assert sync.current["status"] == "failed" and "分页重复" in sync.current["error"]
        assert not plugin.requests
    asyncio.run(scenario())


def test_automatic_schedule_and_account_change(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        await sync.start()
        sync.save_settings(SyncSettings(**dict(sync.settings(), enabled=True)))
        for _ in range(100):
            if sync.task and sync.task.done():
                break
            await asyncio.sleep(.02)
        assert sync.current["automatic"] and sync.current["status"] == "completed"
        assert sync.next_run_at > time.time()
        plugin.store.save_account("jm", {"account": "different", "token": "different"})
        with pytest.raises(ComicApiError) as error:
            sync.launch(automatic=True)
        assert error.value.code == "account_changed" and not sync.settings()["enabled"]
        await sync.stop()
    asyncio.run(scenario())


def test_directory_bounds_normalization_and_symlinks(tmp_path, monkeypatch):
    paths = DownloadPaths(Store(tmp_path / "data"))
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
    monkeypatch.setenv("JM_DOWNLOAD_ROOTS", str(extra))
    assert DownloadPaths(Store(tmp_path / "other-data")).roots == [extra]
    assert safe_name("CON") == "_CON"
    assert "/" not in safe_name("../../name:bad")


@pytest.mark.parametrize("rule", ["id", "title", "author"])
def test_directory_rules_are_contained_and_id_stable(tmp_path, rule):
    sync, plugin = make_sync(tmp_path)
    book = plugin.detail("101")
    book["title"] = "../../漫画\\标题"
    book["author"] = "CON"
    chapter = {"id": "1011", "name": "../../章节"}
    config = dict(sync.settings(), directory_rule=rule)
    path = sync.paths.chapter(config, book, chapter, 1)
    assert path.is_relative_to(Path(config["download_dir"]))
    assert "101" in str(path) and "1011" in str(path)
    assert len(path.relative_to(config["download_dir"]).parts) == (3 if rule == "author" else 2)


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
    config = SyncSettings(speed_limit_kbps=4, retries=0).model_dump()
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
        data, extension, received = receive_image(plugin, url, "1", SyncSettings(speed_limit_kbps=8, retries=0).model_dump(), threading.Event())
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


def test_incomplete_favorite_scan_is_not_marked_completed(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        plugin.favorites = lambda **kwargs: {"items": [{"id": "101"}], "total": 3, "has_next": False}
        sync.launch()
        await sync.task
        assert sync.current["status"] == "failed"
        assert "数量不一致" in sync.current["error"] and not plugin.requests
    asyncio.run(scenario())


@pytest.fixture
def jm_client(tmp_path):
    from main import create_app
    sync, plugin = make_sync(tmp_path)
    with TestClient(create_app(sync.store, {"jm": plugin})) as client:
        yield client


def wait_for_run(client):
    for _ in range(200):
        data = client.get("/api/jm/sync").json()
        if not data["active"]:
            return data["history"][0]
        time.sleep(.01)
    pytest.fail("sync did not finish")


def test_sync_api_and_static_ui(jm_client):
    client = jm_client
    assert client.get("/api/jm/favorites").json()["items"]
    assert client.get("/api/jm/favorites?page=0").status_code == 422
    assert client.get("/api/jm/favorites?order=invalid").status_code == 422
    settings = client.get("/api/jm/sync/settings").json()["settings"]
    assert not settings["enabled"]
    assert client.put("/api/jm/sync/settings", json=dict(settings, speed_limit_kbps=-1)).status_code == 422
    assert client.put("/api/jm/sync/settings", json=dict(settings, download_dir="/etc")).status_code == 422
    assert client.get("/api/jm/sync/directories?path=/etc").status_code == 422
    assert client.post("/api/jm/sync/run", json={"album_ids": ["../1"]}).status_code == 422
    assert client.post("/api/jm/sync/run", json={"album_ids": []}).status_code == 422
    assert client.post("/api/jm/sync/run", json={"album_ids": ["101"]}).status_code == 202
    assert wait_for_run(client)["status"] == "completed"
    assert client.post("/api/jm/sync/cancel").status_code == 409
    assert client.get("/static/jm-sync.js?v=test").status_code == 200
    html = client.get("/").text
    assert 'id="jm-favorites"' in html and '/static/jm-sync.js?v=' in html
    assert client.delete("/api/sources/jm/account").status_code == 200
    assert client.get("/api/jm/favorites").status_code == 401
    assert client.post("/api/jm/sync/run", json={}).status_code == 401


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
    assert "更换或解除账号绑定后，定时同步自动关闭" in html


def test_active_sync_blocks_login_and_logout(jm_client):
    client = jm_client
    client.app.state.service.sources["jm"].delay = .2
    assert client.post("/api/jm/sync/run", json={}).status_code == 202
    assert client.delete("/api/sources/jm/account").status_code == 409
    assert client.post("/api/sources/jm/login", json={"account": "new", "password": "secret"}).status_code == 409
    assert client.post("/api/jm/sync/cancel").status_code == 200
    assert wait_for_run(client)["status"] == "cancelled"


def test_legacy_interval_migrates_to_cron_and_schedules_next_run(tmp_path):
    from datetime import datetime
    sync, _ = make_sync(tmp_path)
    saved = sync.store.settings(sync.settings_key)
    saved.pop("cron")
    sync.store.save_settings(sync.settings_key, dict(saved, interval_minutes=360))
    assert sync.settings()["cron"] == "0 */6 * * *"
    assert sync.options()["schedule"] == "每 6 小时整点"
    sync.save_settings(SyncSettings(**dict(sync.settings(), cron="30 3 * * *")))
    sync._schedule_next()
    run = datetime.fromtimestamp(sync.next_run_at)
    assert (run.hour, run.minute) == (3, 30) and 0 < sync.next_run_at - time.time() <= 86400
    assert sync.store.settings(sync.clock_key)["next_run_at"] == sync.next_run_at
