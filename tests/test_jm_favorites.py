import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from src.clients.jm import JmClient
from src.errors import ComicApiError
from src.sources.jm import JmPlugin
from src.storage import Store


def response(data, status=200):
    return SimpleNamespace(content=json.dumps(data).encode(), status_code=status, cookies={})


class FavoriteRemote:
    def __init__(self):
        self.ids = {str(value) for value in range(101, 122)}
        self.calls = []
        self.failure = ""
        self.delay = 0

    @property
    def posts(self):
        return [call for call in self.calls if call[0] == "POST"]

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert urlparse(url).path == "/favorite"
        if method == "GET":
            assert kwargs["params"]["folder_id"] == "0"
            page = int(kwargs["params"]["page"])
            ids = sorted(self.ids, key=int)[(page - 1) * 20:page * 20]
            return response({"list": [{"id": value, "name": f"漫画 {value}"} for value in ids],
                             "folder_list": [], "total": str(len(self.ids))})
        time.sleep(self.delay)
        if self.failure == "network":
            raise TimeoutError("lost response")
        if self.failure == "http500":
            return response({}, 500)
        if self.failure == "invalid_json":
            return SimpleNamespace(content=b"not json", status_code=200)
        if self.failure == "invalid_data":
            return response({"code": 200, "data": "not encrypted data"})
        if self.failure == "unauthorized":
            return response({}, 401)
        if self.failure == "redirect":
            return response({}, 307)
        value = kwargs["data"]["aid"]
        if value in self.ids:
            self.ids.remove(value)
            operation = "remove"
        else:
            self.ids.add(value)
            operation = "add"
        if self.failure == "applied_timeout":
            raise TimeoutError("applied but response lost")
        if self.failure == "conflict":
            self.ids.discard(value)
            operation = "remove"
        if self.failure == "malformed_success":
            return response({"status": "ok"})
        return response({"status": "ok", "type": operation})


@pytest.fixture
def favorite_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "default-data"))
    store = Store(tmp_path / "store")
    store.save_account("jm", {"account": "test-account", "password": "test-password", "token": "test-session"})
    plugin = JmPlugin(store)
    client = plugin.client
    client.host_resolved = True
    client.api_bases = ["https://first.invalid", "https://second.invalid"]
    remote = FavoriteRemote()
    monkeypatch.setattr(client, "request", remote.request)
    return client, remote, plugin, store


def test_add_favorite_checks_all_pages_and_is_idempotent(favorite_client):
    client, remote, _, _ = favorite_client
    result = client.add_favorite("000123")
    assert result == {"success": True, "comic_id": "123", "favorited": True, "already_favorited": False}
    assert [call[2]["params"]["page"] for call in remote.calls if call[0] == "GET"] == ["1", "2"]
    assert len(remote.posts) == 1
    assert remote.posts[0][2]["data"] == {"aid": "123"}
    assert remote.posts[0][2]["allow_redirects"] is False
    assert remote.posts[0][2]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert remote.posts[0][2]["headers"]["Authorization"] == "Bearer test-session"
    assert client.add_favorite("123")["already_favorited"]
    assert "123" in remote.ids and len(remote.posts) == 1


def test_already_favorited_on_later_page_is_not_toggled(favorite_client):
    client, remote, _, _ = favorite_client
    assert client.add_favorite("121")["already_favorited"]
    assert not remote.posts and "121" in remote.ids
    assert len(remote.calls) == 2


def test_add_to_empty_collection(favorite_client):
    client, remote, _, _ = favorite_client
    remote.ids.clear()
    assert not client.add_favorite("123")["already_favorited"]
    assert remote.ids == {"123"}


@pytest.mark.parametrize("comic_id", ["", "0", "-1", "abc", "１２３", "1" * 21, "123?x=1"])
def test_invalid_ids_never_reach_upstream(favorite_client, comic_id):
    client, remote, _, _ = favorite_client
    with pytest.raises(ComicApiError) as error:
        client.add_favorite(comic_id)
    assert error.value.code == "invalid_request" and not remote.calls


def test_add_requires_account_before_network(favorite_client):
    client, remote, plugin, _ = favorite_client
    plugin.logout()
    with pytest.raises(ComicApiError) as error:
        client.add_favorite("123")
    assert error.value.code == "login_required" and not remote.calls


@pytest.mark.parametrize("failure", ["network", "http500", "invalid_json", "invalid_data", "redirect",
                                      "unauthorized", "malformed_success"])
def test_toggle_is_not_replayed_on_any_failure(favorite_client, failure):
    client, remote, _, _ = favorite_client
    remote.failure = failure
    with pytest.raises(ComicApiError) as error:
        client.add_favorite("123")
    assert error.value.code == ("login_required" if failure == "unauthorized" else "favorite_unknown")
    assert len(remote.posts) == 1
    assert remote.posts[0][1] == "https://first.invalid/favorite"


def test_lost_response_retry_reads_actual_state_without_removing(favorite_client):
    client, remote, _, _ = favorite_client
    remote.failure = "applied_timeout"
    with pytest.raises(ComicApiError) as error:
        client.add_favorite("123")
    assert error.value.code == "favorite_unknown" and "123" in remote.ids
    remote.failure = ""
    assert client.add_favorite("123")["already_favorited"]
    assert "123" in remote.ids and len(remote.posts) == 1


def test_two_concurrent_additions_only_toggle_once(favorite_client):
    client, remote, _, _ = favorite_client
    remote.delay = .05
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(client.add_favorite, ["123", "123"]))
    assert {result["already_favorited"] for result in results} == {True, False}
    assert len(remote.posts) == 1 and "123" in remote.ids


def test_logout_waits_until_preflight_and_write_complete(favorite_client, monkeypatch):
    client, remote, plugin, _ = favorite_client
    entered, release = threading.Event(), threading.Event()
    request = remote.request

    def blocked_request(method, url, **kwargs):
        if method == "POST":
            entered.set()
            assert release.wait(2)
        return request(method, url, **kwargs)

    monkeypatch.setattr(client, "request", blocked_request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        addition = pool.submit(client.add_favorite, "123")
        assert entered.wait(2)
        logout = pool.submit(plugin.logout)
        try:
            time.sleep(.03)
            assert not logout.done()
        finally:
            release.set()
        assert addition.result()["favorited"]
        logout.result()
    assert "123" in remote.ids and not client.has_session()


@pytest.mark.parametrize("pages", [
    [{"items": [], "total": 1, "has_next": False}],
    [{"items": [{"id": "101"}], "total": 2, "has_next": True},
     {"items": [{"id": "101"}], "total": 2, "has_next": False}],
    [{"items": [{"id": "101"}], "total": 2, "has_next": True},
     {"items": [{"id": "102"}], "total": 3, "has_next": False}],
    [{"items": [{"id": "bad"}], "total": 1, "has_next": False}],
])
def test_incomplete_or_changing_collection_never_writes(favorite_client, monkeypatch, pages):
    client, remote, _, _ = favorite_client
    iterator = iter(pages)
    monkeypatch.setattr(client, "favorites", lambda **kwargs: next(iterator))
    with pytest.raises(ComicApiError):
        client.add_favorite("123")
    assert not remote.posts


def test_scan_failure_never_writes(favorite_client, monkeypatch):
    client, remote, _, _ = favorite_client

    def broken(**kwargs):
        raise ComicApiError("收藏查询失败", 502, "upstream_error", "jm")

    monkeypatch.setattr(client, "favorites", broken)
    with pytest.raises(ComicApiError, match="收藏查询失败"):
        client.add_favorite("123")
    assert not remote.posts


def test_external_conflict_reports_remove_without_compensating(favorite_client):
    client, remote, _, _ = favorite_client
    remote.failure = "conflict"
    with pytest.raises(ComicApiError) as error:
        client.add_favorite("123")
    assert error.value.code == "favorite_conflict" and "取消收藏" in str(error.value)
    assert len(remote.posts) == 1


def test_add_favorite_api_is_separate_from_local_library(favorite_client):
    from main import create_app
    _, remote, plugin, store = favorite_client
    with TestClient(create_app(store, {"jm": plugin})) as web:
        result = web.post("/api/jm/favorites/123")
        assert result.status_code == 200 and not result.json()["already_favorited"]
        assert web.post("/api/jm/favorites/123").json()["already_favorited"]
        assert web.get("/api/library").json()["items"] == []
        assert "123" in remote.ids and len(remote.posts) == 1
        assert "add_favorite" in web.get("/api/sources").json()["sources"][0]["capabilities"]
        for value in ("0", "abc", "１２３", "1" * 21):
            assert web.post(f"/api/jm/favorites/{value}").status_code == 422
        web.app.state.jm_sync.account_changing = True
        assert web.post("/api/jm/favorites/124").status_code == 409
        web.app.state.jm_sync.account_changing = False
        plugin.capabilities = [cap for cap in plugin.capabilities if cap != "add_favorite"]
        assert web.post("/api/jm/favorites/124").status_code == 400
        assert len(remote.posts) == 1
        plugin.logout()
        assert web.post("/api/jm/favorites/124").status_code == 401


def test_status_reads_already_pulled_catalog_without_upstream(favorite_client):
    client, remote, _, _ = favorite_client
    client.favorites(page=1)
    reads = len(remote.calls)
    assert client.favorite_status("101")["favorited"] is True
    assert client.favorite_status("121")["favorited"] is None
    assert client.favorite_status("123")["favorited"] is None
    assert len(remote.calls) == reads == 1


def test_status_with_no_pulled_data_stays_unknown_without_network(favorite_client):
    client, remote, _, _ = favorite_client
    assert client.favorite_status("101")["favorited"] is None
    assert not remote.calls


def test_complete_pagination_updates_table_and_confirms_absence(favorite_client):
    client, remote, _, _ = favorite_client
    client.favorites(page=1)
    client.favorites(page=2)
    reads = len(remote.calls)
    assert client.favorite_status("101")["favorited"]
    assert client.favorite_status("121")["favorited"]
    assert client.favorite_status("123")["favorited"] is False
    assert len(remote.calls) == reads == 2
    remote.ids.remove("101")
    client.favorites(page=1)
    assert client.favorite_status("101")["favorited"] is False


def test_partial_refresh_does_not_delete_unseen_favorites(favorite_client):
    client, remote, _, store = favorite_client
    owner = store.account_owner("test-account")
    store.record_jm_favorites(owner, ["121", "999"])
    client.favorites(page=1)
    assert client.favorite_status("121")["favorited"]
    assert client.favorite_status("999")["favorited"]
    assert client.favorite_status("123")["favorited"] is None
    client.favorites(page=2)
    assert client.favorite_status("999")["favorited"] is False


def test_empty_complete_pull_confirms_empty_collection(favorite_client):
    client, remote, _, _ = favorite_client
    remote.ids.clear()
    client.favorites()
    assert client.favorite_status("101")["favorited"] is False
    assert client.favorite_status("102")["favorited"] is False
    assert len(remote.calls) == 1


def test_concurrent_table_reads_never_query_remote(favorite_client):
    client, remote, _, _ = favorite_client
    client.favorites(page=1)
    client.favorites(page=2)
    reads = len(remote.calls)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(client.favorite_status, ["101", "102", "121", "123"]))
    assert [result["favorited"] for result in results] == [True, True, True, False]
    assert len(remote.calls) == reads and not remote.posts


def test_failed_page_does_not_clear_or_complete_catalog(favorite_client, monkeypatch):
    client, remote, _, _ = favorite_client
    client.favorites(page=1)
    original = client.request
    monkeypatch.setattr(client, "request", lambda *a, **k: response({"list": [], "folder_list": [], "total": "invalid"}))
    with pytest.raises(ComicApiError):
        client.favorites(page=2)
    assert client.favorite_status("101")["favorited"]
    assert client.favorite_status("123")["favorited"] is None
    monkeypatch.setattr(client, "request", original)
    client.favorites(page=2)
    assert client.favorite_status("123")["favorited"] is None


def test_table_does_not_replace_live_add_preflight(favorite_client):
    client, remote, _, _ = favorite_client
    client.favorites(page=1)
    client.favorites(page=2)
    assert client.favorite_status("123")["favorited"] is False
    remote.ids.add("123")
    assert client.add_favorite("123")["already_favorited"]
    assert not remote.posts
    reads = len(remote.calls)
    assert client.favorite_status("123")["favorited"]
    assert len(remote.calls) == reads
    remote.ids.remove("123")
    assert not client.add_favorite("123")["already_favorited"]
    assert len(remote.posts) == 1 and "123" in remote.ids
    reads = len(remote.calls)
    assert client.favorite_status("123")["favorited"]
    assert len(remote.calls) == reads


def test_lost_write_response_keeps_unknown_until_next_pull(favorite_client):
    client, remote, _, _ = favorite_client
    remote.failure = "applied_timeout"
    with pytest.raises(ComicApiError):
        client.add_favorite("123")
    reads = len(remote.calls)
    assert client.favorite_status("123")["favorited"] is None
    assert len(remote.calls) == reads
    remote.failure = ""
    client.favorites(page=1)
    client.favorites(page=2)
    reads = len(remote.calls)
    assert client.favorite_status("123")["favorited"]
    assert len(remote.calls) == reads and len(remote.posts) == 1


def test_pulled_catalog_persists_without_ttl_or_network(favorite_client, monkeypatch):
    client, remote, _, store = favorite_client
    client.favorites(page=1)
    reopened = JmClient(Store(store.root))
    monkeypatch.setattr(reopened, "request", lambda *a, **k: pytest.fail("status must not request upstream"))
    monkeypatch.setattr(reopened, "ensure_authenticated", lambda: pytest.fail("status must not restore session"))
    assert reopened.favorite_status("101")["favorited"]
    assert reopened.favorite_status("121")["favorited"] is None
    assert len(remote.calls) == 1


def test_favorite_catalog_is_isolated_on_account_change(favorite_client, monkeypatch):
    client, remote, plugin, _ = favorite_client
    client.favorites()
    assert client.favorite_status("101")["favorited"]
    original = remote.request
    def request(method, url, **kwargs):
        if urlparse(url).path == "/login":
            return response({"jwttoken": "new-demo-session"})
        return original(method, url, **kwargs)
    monkeypatch.setattr(client, "request", request)
    remote.ids.clear()
    client.login("other-test-account", "test-password")
    assert client.favorite_status("101")["favorited"] is None
    client.favorites()
    assert client.favorite_status("101")["favorited"] is False
    client.login("test-account", "test-password")
    assert client.favorite_status("101")["favorited"]
    plugin.logout()
    with pytest.raises(ComicApiError) as error:
        client.favorite_status("101")
    assert error.value.code == "login_required"


def test_custom_folder_pull_does_not_clear_global_catalog(favorite_client, monkeypatch):
    client, _, _, store = favorite_client
    owner = store.account_owner("test-account")
    store.record_jm_favorites(owner, ["101", "999"])
    payload = {"list": [{"id": "102", "name": "Scoped"}],
               "folder_list": [{"FID": "7", "name": "Scope"}], "total": "1"}
    monkeypatch.setattr(client, "jm_request", lambda *a, **k: payload)
    client.favorites(folder_id="7")
    assert client.favorite_status("101")["favorited"]
    assert client.favorite_status("102")["favorited"]
    assert client.favorite_status("999")["favorited"]
    assert client.favorite_status("103")["favorited"] is None


def test_out_of_order_pages_never_clear_unseen_rows(favorite_client):
    client, _, _, store = favorite_client
    owner = store.account_owner("test-account")
    store.record_jm_favorites(owner, ["999"])
    client.favorites(page=2)
    client.favorites(page=1)
    assert client.favorite_status("999")["favorited"]
    assert client.favorite_status("123")["favorited"] is None


def test_expired_session_does_not_trigger_login_when_reading_table(favorite_client, monkeypatch):
    client, remote, _, _ = favorite_client
    client.favorites(page=1)
    client.clear_session()
    reads = len(remote.calls)
    monkeypatch.setattr(client, "login", lambda *a, **k: pytest.fail("table read must not log in"))
    assert client.favorite_status("101")["favorited"]
    assert len(remote.calls) == reads


def test_favorite_status_is_read_only_and_survives_reopening(favorite_client):
    from main import create_app
    _, remote, plugin, store = favorite_client
    with TestClient(create_app(store, {"jm": plugin})) as web:
        assert web.get("/api/jm/favorites/121").json()["favorited"] is None
        assert not remote.calls
        assert web.get("/api/jm/favorites?page=1").status_code == 200
        assert web.get("/api/jm/favorites?page=2").status_code == 200
        result = web.get("/api/jm/favorites/121")
        assert result.status_code == 200 and result.json() == {"comic_id": "121", "favorited": True}
        assert result.headers["cache-control"] == "no-store"
        assert web.get("/api/jm/favorites/123").json()["favorited"] is False
        assert not remote.posts
        assert web.post("/api/jm/favorites/123").status_code == 200
        assert web.get("/api/jm/favorites/123").json()["favorited"] is True
        assert len(remote.posts) == 1
        for value in ("0", "abc", "１２３", "1" * 21):
            assert web.get(f"/api/jm/favorites/{value}").status_code == 422
        remote.ids.remove("123")
        assert web.get("/api/jm/favorites/123").json()["favorited"] is True
        web.get("/api/jm/favorites?page=1")
        web.get("/api/jm/favorites?page=2")
        assert web.get("/api/jm/favorites/123").json()["favorited"] is False
        assert len(remote.posts) == 1
        remote.ids.add("123")
        web.get("/api/jm/favorites?page=1")
        web.get("/api/jm/favorites?page=2")
    reopened = JmPlugin(store)
    reopened.client.host_resolved = True
    reopened.client.request = remote.request
    with TestClient(create_app(store, {"jm": reopened})) as web:
        assert web.get("/api/jm/favorites/123").json()["favorited"] is True
        assert len(remote.posts) == 1
        reopened.logout()
        assert web.get("/api/jm/favorites/123").status_code == 401
    assert store.account("jm") == {}


def test_status_api_reads_table_without_source_business_call(favorite_client, monkeypatch):
    from main import create_app
    client, remote, plugin, store = favorite_client
    client.favorites(page=1)
    reads = len(remote.calls)
    with TestClient(create_app(store, {"jm": plugin})) as web:
        async def unexpected(*args, **kwargs):
            pytest.fail("status route must read SQL directly")
        monkeypatch.setattr(web.app.state.service, "call", unexpected)
        assert web.get("/api/jm/favorites/101").json()["favorited"] is True
        assert web.get("/api/jm/favorites/123").json()["favorited"] is None
    assert len(remote.calls) == reads


def test_favorite_status_unknown_is_null_and_never_queries_remote(favorite_client, monkeypatch):
    from main import create_app
    client, remote, plugin, store = favorite_client
    monkeypatch.setattr(client, "favorites", lambda **kwargs: pytest.fail("unexpected remote scan"))
    with TestClient(create_app(store, {"jm": plugin})) as web:
        result = web.get("/api/jm/favorites/123")
        assert result.status_code == 200 and result.json()["favorited"] is None
    assert not remote.calls


def test_add_favorite_api_preserves_unknown_and_conflict_errors(favorite_client):
    from main import create_app
    _, remote, plugin, store = favorite_client
    with TestClient(create_app(store, {"jm": plugin})) as web:
        remote.failure = "network"
        result = web.post("/api/jm/favorites/123")
        assert result.status_code == 502 and result.json()["error"]["code"] == "favorite_unknown"
        remote.failure = "conflict"
        result = web.post("/api/jm/favorites/123")
        assert result.status_code == 409 and result.json()["error"]["code"] == "favorite_conflict"
