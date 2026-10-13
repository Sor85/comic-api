import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from src.auth import AuthManager, AuthSettings, AuthUpdate
from src.errors import ComicApiError
from src.storage import Store
from tests.fakes import DemoPlugin
from tests.test_auth import basic_headers


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "default-data"))
    from main import create_app

    def create():
        store = Store(tmp_path / "store")
        return create_app(store, {"demo": DemoPlugin(store)})

    return create


@pytest.fixture
def client(app_factory):
    with TestClient(app_factory()) as client:
        yield client


def save(client, *, headers=None, **values):
    current = client.get("/api/settings/auth", headers=headers).json()
    body = {"enabled": current["enabled"], "username": current["username"], "revision": current["revision"]}
    return client.put("/api/settings/auth", json={**body, **values}, headers=headers)


def test_default_status_is_readable_without_password_and_not_persisted(client):
    result = client.get("/api/settings/auth")
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    status = result.json()
    assert status["enabled"] is False and status["username"] == ""
    assert status["password_configured"] is False and status["storage"] == "environment"
    assert set(status) == {"enabled", "username", "password_configured", "revision", "storage"}
    assert client.app.state.store.settings("site_auth") == {}


def test_save_credentials_while_disabled_uses_salted_verifier_and_no_source_account(client):
    result = save(client, username="reader", password="a-private-password")
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.json()["reauthenticate"] is False
    status = result.json()["settings"]
    assert status["password_configured"] and not status["enabled"] and status["storage"] == "database"
    assert "a-private-password" not in result.text
    assert "password_hash" not in result.text and "password_salt" not in result.text
    record = client.app.state.store.settings("site_auth")
    assert "a-private-password" not in json.dumps(record)
    assert len(record["password_hash"]) == 64 and len(record["password_salt"]) == 32
    assert client.app.state.store.account("demo") is None
    assert client.get("/api/library").status_code == 200
    assert save(client, username="reader", password="a-private-password").status_code == 200
    other = client.app.state.store.settings("site_auth")
    assert record["password_salt"] != other["password_salt"]
    assert record["password_hash"] != other["password_hash"]


def test_enable_rotate_and_disable_are_effective_without_restarting(client):
    result = save(client, enabled=True, username="reader", password="old-secret")
    assert result.status_code == 200 and result.json()["reauthenticate"] is True
    old_headers = basic_headers("reader", "old-secret")
    for path in ("/", "/api/settings/auth", "/api/library", "/docs", "/static/app.js"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers=old_headers).status_code == 200
    assert client.get("/health").status_code == 200
    result = save(client, headers=old_headers, username="new-reader", password="new-secret")
    assert result.status_code == 200 and result.json()["reauthenticate"] is True
    assert client.get("/api/library", headers=old_headers).status_code == 401
    new_headers = basic_headers("new-reader", "new-secret")
    assert client.get("/api/library", headers=new_headers).status_code == 200
    result = save(client, headers=new_headers, enabled=False)
    assert result.status_code == 200 and result.json()["reauthenticate"] is False
    assert result.json()["settings"]["password_configured"] is True
    assert client.get("/api/library").status_code == 200
    # Closing authentication preserves credentials and permits re-enabling them.
    assert save(client, enabled=True).status_code == 200
    assert client.get("/api/library").status_code == 401
    assert client.get("/api/library", headers=new_headers).status_code == 200


@pytest.mark.parametrize("password", [None, ""])
def test_blank_password_keeps_current_verifier_when_renaming(client, password):
    assert save(client, enabled=True, username="reader", password="same-secret").status_code == 200
    record = client.app.state.store.settings("site_auth")
    result = save(client, headers=basic_headers("reader", "same-secret"), username="renamed", password=password)
    assert result.status_code == 200 and result.json()["reauthenticate"]
    assert client.app.state.store.settings("site_auth")["password_hash"] == record["password_hash"]
    assert client.get("/api/library", headers=basic_headers("reader", "same-secret")).status_code == 401
    assert client.get("/api/library", headers=basic_headers("renamed", "same-secret")).status_code == 200


def test_saved_configuration_wins_over_environment_and_survives_restart(app_factory, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_USERNAME", "environment-reader")
    monkeypatch.setenv("AUTH_PASSWORD", "environment-secret")
    with TestClient(app_factory()) as client:
        headers = basic_headers("environment-reader", "environment-secret")
        assert save(client, headers=headers, username="saved-reader", password="saved-secret").status_code == 200
    with TestClient(app_factory()) as client:
        assert client.get("/api/settings/auth").status_code == 401
        assert client.get("/api/library", headers=headers).status_code == 401
        headers = basic_headers("saved-reader", "saved-secret")
        status = client.get("/api/settings/auth", headers=headers).json()
        assert status["enabled"] is True and status["storage"] == "database"
        assert status["username"] == "saved-reader"
        assert save(client, headers=headers, enabled=False).status_code == 200
    with TestClient(app_factory()) as client:
        status = client.get("/api/settings/auth").json()
        assert status["enabled"] is False and status["password_configured"] is True
        assert client.get("/").status_code == 200


def test_persisted_settings_do_not_depend_on_stale_or_incomplete_environment(app_factory, monkeypatch):
    with TestClient(app_factory()) as client:
        assert save(client, enabled=True, username="reader", password="saved-secret").status_code == 200
    monkeypatch.setenv("AUTH_ENABLED", "invalid-old-value")
    monkeypatch.setenv("AUTH_USERNAME", "")
    monkeypatch.setenv("AUTH_PASSWORD", "")
    with TestClient(app_factory()) as client:
        assert client.get("/api/library").status_code == 401
        assert client.get("/api/library", headers=basic_headers("reader", "saved-secret")).status_code == 200


def test_whitespace_environment_password_cannot_be_enabled_from_settings(app_factory, monkeypatch):
    monkeypatch.setenv("AUTH_USERNAME", "reader")
    monkeypatch.setenv("AUTH_PASSWORD", "   ")
    with TestClient(app_factory()) as client:
        assert client.get("/api/settings/auth").json()["password_configured"] is False
        assert save(client, enabled=True).status_code == 422


def test_persisted_password_verification_and_cache_invalidation(app_factory):
    with TestClient(app_factory()) as client:
        assert save(client, enabled=True, username="reader", password="old-secret").status_code == 200
    with TestClient(app_factory()) as client:
        assert client.app.state.auth._verified_digest is None
        assert client.get("/api/library", headers=basic_headers("reader", "wrong-secret")).status_code == 401
        assert client.app.state.auth._verified_digest is None
        assert client.get("/api/library", headers=basic_headers("reader", "old-secret")).status_code == 200
        assert client.app.state.auth._verified_digest is not None
        assert save(client, headers=basic_headers("reader", "old-secret"), password="new-secret").status_code == 200
        assert client.get("/api/library", headers=basic_headers("reader", "old-secret")).status_code == 401
        assert client.get("/api/library", headers=basic_headers("reader", "new-secret")).status_code == 200


@pytest.mark.parametrize("values", [
    {"enabled": True}, {"enabled": True, "username": "reader"},
    {"enabled": True, "password": "secret"}, {"username": "user:name"},
    {"username": "user\nname"}, {"password": "  "}, {"password": "secret\x00value"},
    {"enabled": "true"}, {"enabled": 1}, {"username": None}, {"username": 42},
    {"username": "x" * 129}, {"password": "x" * 1025}, {"password": 123},
    {"password_hash": "injected"}, {"revision": ""},
])
def test_invalid_updates_do_not_mutate_active_or_stored_settings(client, values):
    before = client.get("/api/settings/auth").json()
    result = client.put("/api/settings/auth", json={"enabled": False, "username": "",
                                                   "revision": before["revision"], **values})
    assert result.status_code == 422
    assert client.get("/api/settings/auth").json() == before
    assert client.app.state.store.settings("site_auth") == {}
    assert "secret" not in result.text and "injected" not in result.text


def test_stale_revision_cannot_overwrite_newer_credentials(client):
    old = client.get("/api/settings/auth").json()
    assert save(client, username="reader", password="secret").status_code == 200
    result = client.put("/api/settings/auth", json={"enabled": True, "username": "other",
                                                   "password": "replacement", "revision": old["revision"]})
    assert result.status_code == 409 and result.json()["error"]["code"] == "auth_settings_changed"
    assert client.get("/api/settings/auth").json()["username"] == "reader"
    assert client.get("/api/library").status_code == 200


def test_protected_settings_cannot_be_read_or_changed_without_current_credentials(client):
    assert save(client, enabled=True, username="reader", password="secret").status_code == 200
    assert client.get("/api/settings/auth").status_code == 401
    assert client.put("/api/settings/auth", json={"enabled": False}).status_code == 401
    assert client.put("/api/settings/auth", headers=basic_headers("reader", "wrong"), json={"enabled": False}).status_code == 401
    assert client.app.state.auth.enabled is True


def test_cross_site_setup_is_blocked_even_when_auth_is_disabled(client):
    before = client.get("/api/settings/auth").json()
    result = client.put("/api/settings/auth", headers={"Origin": "https://foreign.invalid"},
                        json={"enabled": True, "username": "attacker", "password": "secret", "revision": before["revision"]})
    assert result.status_code == 403
    assert client.get("/api/settings/auth").json() == before


def test_failed_storage_write_does_not_publish_new_credentials(client, monkeypatch):
    manager = client.app.state.auth
    before = manager.status()
    monkeypatch.setattr(manager.store, "save_settings", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        client.put("/api/settings/auth", json={"enabled": True, "username": "reader", "password": "secret",
                                               "revision": before["revision"]})
    assert manager.status() == before and manager.enabled is False
    assert client.get("/api/library").status_code == 200


@pytest.mark.parametrize("saved", [{}, None, [], {"enabled": False},
                                   {"enabled": "false", "username": "", "password_salt": "", "password_hash": "", "revision": "a" * 32}])
def test_corrupt_saved_auth_fails_closed_instead_of_defaulting_to_open(tmp_path, saved):
    store = Store(tmp_path)
    store.save_settings("site_auth", saved)
    with pytest.raises(ValueError, match="已保存的站点鉴权配置无效"):
        AuthManager(store, AuthSettings())


def test_concurrent_password_updates_cannot_overwrite_newer_state(tmp_path, monkeypatch):
    import src.auth as module

    async def scenario():
        manager = AuthManager(Store(tmp_path), AuthSettings())
        before = manager.status()
        started, release = threading.Event(), threading.Event()
        original = module.password_hash

        def delayed(password, salt):
            started.set()
            assert release.wait(5)
            return original(password, salt)

        monkeypatch.setattr(module, "password_hash", delayed)
        first = asyncio.create_task(manager.save(AuthUpdate(enabled=True, username="first", password="secret", revision=before["revision"])))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            await manager.save(AuthUpdate(enabled=False, username="second", revision=before["revision"]))
        finally:
            release.set()
        with pytest.raises(ComicApiError) as error:
            await first
        assert error.value.code == "auth_settings_changed"
        assert manager.status()["username"] == "second" and not manager.enabled

    asyncio.run(scenario())
