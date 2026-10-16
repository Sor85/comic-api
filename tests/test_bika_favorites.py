import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from src.errors import ComicApiError
from src.sources.bika import BikaPlugin
from src.storage import Store


def comic_id(value):
    return f"{value:024x}"


def response(data, status=200):
    return SimpleNamespace(status_code=status, json=lambda: data)


class FavoriteRemote:
    def __init__(self):
        self.ids = {comic_id(value) for value in range(1, 4)}
        self.calls = []
        self.failure = ""
        self.delay = 0

    @property
    def posts(self):
        return [call for call in self.calls if call[0] == "POST"]

    def request(self, method, url, **kwargs):
        path = urlparse(url).path
        self.calls.append((method, path, kwargs))
        if path == "/users/favourite":
            page = int(kwargs["params"]["page"])
            ids = sorted(self.ids)[(page - 1) * 2:page * 2]
            docs = [{"_id": value, "title": f"漫画 {value}", "author": "作者", "thumb": {
                "fileServer": "https://images.invalid", "path": "cover.jpg"}} for value in ids]
            return response({"data": {"comics": {"docs": docs, "total": len(self.ids), "limit": 2,
                                                 "page": page, "pages": math.ceil(len(self.ids) / 2)}}})
        if path == "/auth/sign-in":
            return response({"data": {"token": "new-session"}})
        if method == "GET":
            value = path.split("/")[-1]
            assert path == f"/comics/{value}"
            return response({"data": {"comic": {"_id": value, "isFavourite": value in self.ids}}})
        assert path.endswith("/favourite")
        value = path.split("/")[-2]
        time.sleep(self.delay)
        if self.failure == "network":
            raise TimeoutError("lost response")
        if self.failure == "http500":
            return response({}, 500)
        if self.failure == "unauthorized":
            return response({}, 401)
        if self.failure == "redirect":
            return response({}, 307)
        if self.failure == "invalid_json":
            def bad_json():
                raise ValueError("invalid JSON")
            return SimpleNamespace(status_code=200, json=bad_json)
        if value in self.ids:
            self.ids.remove(value)
            action = "un_favourite"
        else:
            self.ids.add(value)
            action = "favourite"
        if self.failure == "applied_timeout":
            raise TimeoutError("applied but response lost")
        if self.failure == "conflict":
            self.ids.discard(value)
            action = "un_favourite"
        if self.failure == "malformed_success":
            return response({"data": {}})
        return response({"data": {"action": action}})


@pytest.fixture
def favorite_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "default-data"))
    store = Store(tmp_path / "store")
    store.save_account("bika", {"account": "test-account", "password": "test-password", "token": "test-session"})
    plugin = BikaPlugin(store)
    plugin.client.api_base = "https://bika.invalid/"
    remote = FavoriteRemote()
    monkeypatch.setattr(plugin.client, "request", remote.request)
    return plugin.client, remote, plugin, store


def test_favorites_pagination_sort_and_local_status(favorite_client):
    client, remote, _, _ = favorite_client
    first = client.favorites(sort="ld")
    assert (first["total"], first["page_size"], first["has_next"]) == (3, 2, True)
    assert first["items"][0]["source"] == "bika"
    assert first["items"][0]["cover"] == "https://images.invalid/static/cover.jpg"
    assert remote.calls[0][2]["params"] == {"s": "ld", "page": "1"}
    assert remote.calls[0][2]["headers"]["authorization"] == "test-session"
    assert client.favorite_status(comic_id(1))["favorited"] is True
    assert client.favorite_status(comic_id(3))["favorited"] is None
    assert client.favorite_status(comic_id(4))["favorited"] is None
    second = client.favorites(page=2, sort="ld")
    assert second["has_next"] is False
    assert client.favorite_status(comic_id(3))["favorited"] is True
    assert client.favorite_status(comic_id(4))["favorited"] is False
    assert len(remote.calls) == 2


def test_partial_out_of_order_and_failed_scans_do_not_mark_absence(favorite_client, monkeypatch):
    client, remote, _, _ = favorite_client
    client.favorites(page=2)
    client.favorites()
    assert client.favorite_status(comic_id(4))["favorited"] is None
    remote.ids.add(comic_id(5))
    client.favorites(page=2)
    assert client.favorite_status(comic_id(4))["favorited"] is None
    client.favorites()
    request = remote.request
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: response({"data": {"comics": {}}}))
    with pytest.raises(ComicApiError):
        client.favorites(page=2)
    monkeypatch.setattr(client, "request", request)
    client.favorites(page=2)
    assert client.favorite_status(comic_id(4))["favorited"] is None


@pytest.mark.parametrize("data", [
    {}, {"data": None}, {"data": {"comics": []}},
    {"data": {"comics": {"docs": [], "page": 1, "pages": 1, "limit": 2, "total": 1}}},
    {"data": {"comics": {"docs": [], "page": True, "pages": 0, "limit": 2, "total": 0}}},
    {"data": {"comics": {"docs": [], "page": 2, "pages": 0, "limit": 2, "total": 0}}},
    {"data": {"comics": {"docs": [], "page": 1, "pages": 0, "limit": 0, "total": 0}}},
    {"data": {"comics": {"docs": [{"_id": "bad", "title": "漫画"}], "page": 1, "pages": 1, "limit": 2, "total": 1}}},
    {"data": {"comics": {"docs": [{"_id": comic_id(1), "title": "漫画"}] * 2, "page": 1, "pages": 1, "limit": 2, "total": 2}}},
])
def test_invalid_lists_are_errors_not_empty_or_complete(favorite_client, monkeypatch, data):
    client, _, _, _ = favorite_client
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: response(data))
    with pytest.raises(ComicApiError) as error:
        client.favorites()
    assert error.value.code == "invalid_response"
    assert client.favorite_status(comic_id(4))["favorited"] is None


def test_empty_collection_is_confirmed_and_new_addition_is_independent(favorite_client):
    client, remote, _, store = favorite_client
    remote.ids.clear()
    assert client.favorites()["items"] == []
    assert client.favorite_status(comic_id(4))["favorited"] is False
    assert not client.add_favorite(comic_id(4))["already_favorited"]
    assert store.books() == []
    assert client.favorite_status(comic_id(4))["favorited"] is True


def test_addition_checks_live_detail_not_cached_status_and_is_idempotent(favorite_client):
    client, remote, _, store = favorite_client
    value = comic_id(10)
    store.set_bika_favorite(client._favorite_owner(), value, True)
    result = client.add_favorite(value.upper())
    assert result == {"success": True, "comic_id": value, "favorited": True, "already_favorited": False}
    assert [call[1] for call in remote.calls] == [f"/comics/{value}", f"/comics/{value}/favourite"]
    post = remote.posts[0][2]
    assert post["allow_redirects"] is False and "data" not in post
    assert client.add_favorite(value)["already_favorited"] is True
    assert value in remote.ids and len(remote.posts) == 1


@pytest.mark.parametrize("value", ["bad", "", "a" * 23, "a" * 25, "g" * 24, "１" * 24, "a" * 24 + "?x=1"])
def test_invalid_ids_do_not_reach_upstream(favorite_client, value):
    client, remote, _, _ = favorite_client
    with pytest.raises(ComicApiError) as error:
        client.add_favorite(value)
    assert error.value.code == "invalid_request" and not remote.calls


@pytest.mark.parametrize("page,sort", [(0, "dd"), (1001, "dd"), (True, "dd"), (1, "bad")])
def test_invalid_list_queries_do_not_reach_upstream(favorite_client, page, sort):
    client, remote, _, _ = favorite_client
    with pytest.raises(ComicApiError) as error:
        client.favorites(page, sort)
    assert error.value.code == "invalid_request" and not remote.calls


@pytest.mark.parametrize("comic", [{}, {"_id": comic_id(10)}, {"_id": comic_id(10), "isFavourite": "false"},
                                    {"_id": comic_id(11), "isFavourite": False}])
def test_missing_or_invalid_live_state_never_toggles(favorite_client, monkeypatch, comic):
    client, remote, _, _ = favorite_client
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: response({"data": {"comic": comic}}))
    with pytest.raises(ComicApiError) as error:
        client.add_favorite(comic_id(10))
    assert error.value.code == "invalid_response" and not remote.posts


@pytest.mark.parametrize("failure", ["network", "http500", "unauthorized", "redirect", "invalid_json", "malformed_success"])
def test_toggle_is_never_replayed_on_failure(favorite_client, failure):
    client, remote, _, _ = favorite_client
    remote.failure = failure
    with pytest.raises(ComicApiError) as error:
        client.add_favorite(comic_id(10))
    assert error.value.code == ("login_required" if failure == "unauthorized" else "favorite_unknown")
    assert len(remote.posts) == 1 and len(remote.calls) == 2
    assert client.favorite_status(comic_id(10))["favorited"] is None


def test_lost_response_retry_reads_actual_state_without_removing(favorite_client):
    client, remote, _, _ = favorite_client
    remote.failure = "applied_timeout"
    with pytest.raises(ComicApiError) as error:
        client.add_favorite(comic_id(10))
    assert error.value.code == "favorite_unknown"
    remote.failure = ""
    assert client.add_favorite(comic_id(10))["already_favorited"]
    assert comic_id(10) in remote.ids and len(remote.posts) == 1


def test_external_conflict_does_not_compensate(favorite_client):
    client, remote, _, _ = favorite_client
    remote.failure = "conflict"
    with pytest.raises(ComicApiError) as error:
        client.add_favorite(comic_id(10))
    assert error.value.code == "favorite_conflict"
    assert client.favorite_status(comic_id(10))["favorited"] is False
    assert len(remote.posts) == 1


def test_concurrent_additions_only_toggle_once(favorite_client):
    client, remote, _, _ = favorite_client
    remote.delay = .03
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(client.add_favorite, [comic_id(10)] * 2))
    assert {result["already_favorited"] for result in results} == {True, False}
    assert len(remote.posts) == 1


def test_logout_waits_for_preflight_and_write(favorite_client, monkeypatch):
    client, remote, plugin, _ = favorite_client
    entered, release = threading.Event(), threading.Event()
    request = remote.request

    def blocked(method, url, **kwargs):
        if method == "POST":
            entered.set()
            assert release.wait(2)
        return request(method, url, **kwargs)

    monkeypatch.setattr(client, "request", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        addition = pool.submit(client.add_favorite, comic_id(10))
        assert entered.wait(2)
        logout = pool.submit(plugin.logout)
        try:
            time.sleep(.03)
            assert not logout.done()
        finally:
            release.set()
        assert addition.result()["favorited"]
        logout.result()
    with pytest.raises(ComicApiError) as error:
        client.favorite_status(comic_id(10))
    assert error.value.code == "login_required"


def test_store_account_isolation_persistence_and_unknown_override(favorite_client):
    client, _, plugin, store = favorite_client
    value = comic_id(1)
    client.favorites()
    original = client._favorite_owner()
    other = store.account_owner("another-account")
    assert store.bika_favorite_status(other, value) is None
    assert Store(store.root).bika_favorite_status(original, value) is True
    plugin.logout()
    store.save_account("bika", {"account": "test-account", "password": "p", "token": "t"})
    assert BikaPlugin(store).favorite_status(value)["favorited"] is True
    store.record_bika_favorites(original, [], complete=True)
    assert store.bika_favorite_status(original, value) is False
    store.set_bika_favorite(original, value, None)
    assert store.bika_favorite_status(original, value) is None
    assert store.bika_favorite_status(other, value) is None


def test_v4_database_migration_preserves_jm_and_library(tmp_path):
    store = Store(tmp_path)
    owner = store.account_owner("existing-account")
    store.record_jm_favorites(owner, ["101"], complete=True)
    store.save_book("jm", "101", {"title": "Existing"}, "Existing category")
    with store.connect() as db:
        db.execute("DROP TABLE bika_favorites")
        db.execute("DROP TABLE bika_favorite_catalog")
        db.execute("PRAGMA user_version=4")
    migrated = Store(tmp_path)
    assert migrated.jm_favorite_status(owner, "101") is True
    assert migrated.books()[0]["library_category"] == "Existing category"
    assert migrated.bika_favorite_status(owner, comic_id(1)) is None
    with migrated.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9


def test_status_does_not_restore_session_or_read_remote(favorite_client):
    client, remote, _, store = favorite_client
    client.authorization = ""
    client._persist_token("")
    assert client.favorite_status(comic_id(1))["favorited"] is None
    assert not remote.calls and not store.account("bika")["token"]


def test_preflight_read_can_refresh_expired_session_but_write_only_once(favorite_client, monkeypatch):
    client, remote, _, _ = favorite_client
    request = remote.request
    expired = False

    def read_expired(method, url, **kwargs):
        nonlocal expired
        if method == "GET" and not expired:
            expired = True
            return response({}, 401)
        return request(method, url, **kwargs)

    monkeypatch.setattr(client, "request", read_expired)
    assert client.add_favorite(comic_id(10))["favorited"]
    toggles = [call for call in remote.posts if call[1].endswith("/favourite")]
    assert len(toggles) == 1 and toggles[0][2]["headers"]["authorization"] == "new-session"


def test_favorites_api_manifest_validation_and_no_store_status(favorite_client):
    from main import create_app
    client, remote, plugin, store = favorite_client
    path = f"/api/bika/favorites/{comic_id(10)}"
    with TestClient(create_app(store, {"bika": plugin})) as web:
        manifest = web.get("/api/sources").json()["sources"][0]
        assert "favorites" in manifest["capabilities"] and "add_favorite" in manifest["capabilities"]
        status = web.get(path)
        assert status.status_code == 200 and status.json()["favorited"] is None
        assert status.headers["cache-control"] == "no-store" and not remote.calls
        listing = web.get("/api/bika/favorites?sort=da")
        assert listing.status_code == 200 and listing.headers["cache-control"] == "no-store"
        assert remote.calls[0][2]["params"]["s"] == "da"
        assert web.post(path).json()["favorited"] is True
        assert web.post(path).json()["already_favorited"] is True
        assert web.get("/api/library").json()["items"] == []
        assert len(remote.posts) == 1
        for query in ("page=0", "page=1001", "sort=bad"):
            assert web.get("/api/bika/favorites?" + query).status_code == 422
        assert web.post("/api/bika/favorites/bad").status_code == 422
        assert web.get("/api/bika/favorites/bad").status_code == 422
        plugin.capabilities = [cap for cap in plugin.capabilities if cap != "add_favorite"]
        assert web.post(path).status_code == 400
        plugin.capabilities.append("add_favorite")
        plugin.logout()
        assert web.get(path).status_code == 401
        assert web.post(path).status_code == 401
        assert web.get("/api/bika/favorites").status_code == 401
        assert len(remote.posts) == 1


def test_remove_favorite_checks_live_detail_and_is_idempotent(favorite_client):
    client, remote, _, store = favorite_client
    value = comic_id(10)
    remote.ids.add(value)
    result = client.remove_favorite(value.upper())
    assert result == {"success": True, "comic_id": value, "favorited": False, "already_favorited": True}
    assert value not in remote.ids and len(remote.posts) == 1
    assert client.favorite_status(value)["favorited"] is False
    assert client.remove_favorite(value)["already_favorited"] is False
    assert len(remote.posts) == 1


def test_remove_favorite_unexpected_action_is_conflict(favorite_client, monkeypatch):
    client, remote, _, _ = favorite_client
    value = comic_id(10)
    remote.ids.add(value)
    request = remote.request

    def wrong_action(method, url, **kwargs):
        if method == "POST":
            remote.calls.append((method, url, kwargs))
            return response({"data": {"action": "favourite"}})
        return request(method, url, **kwargs)

    monkeypatch.setattr(client, "request", wrong_action)
    with pytest.raises(ComicApiError) as error:
        client.remove_favorite(value)
    assert error.value.code == "favorite_conflict"
    assert client.favorite_status(value)["favorited"] is True


def test_remove_favorite_api(favorite_client):
    from main import create_app
    client, remote, plugin, store = favorite_client
    value = comic_id(10)
    remote.ids.add(value)
    with TestClient(create_app(store, {"bika": plugin})) as web:
        assert "remove_favorite" in web.get("/api/sources").json()["sources"][0]["capabilities"]
        assert web.delete(f"/api/bika/favorites/{value}").json()["favorited"] is False
        assert web.delete("/api/bika/favorites/bad").status_code == 422
        plugin.capabilities = [cap for cap in plugin.capabilities if cap != "remove_favorite"]
        assert web.delete(f"/api/bika/favorites/{value}").status_code == 400
        assert len(remote.posts) == 1


def test_remove_ignores_stale_local_state_and_never_toggles_absent_comic(favorite_client):
    client, remote, _, store = favorite_client
    value = comic_id(20)
    store.set_bika_favorite(client._favorite_owner(), value, True)
    assert client.remove_favorite(value)["already_favorited"] is False
    assert not remote.posts and value not in remote.ids
    assert client.favorite_status(value)["favorited"] is False
