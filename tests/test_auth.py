import base64
import re
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from src.auth import AuthSettings
from src.storage import Store
from tests.fakes import DemoPlugin


USERNAME = "site-reader"
PASSWORD = "site-password:for-tests"


def basic_headers(username=USERNAME, password=PASSWORD):
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "default-data"))
    from main import create_app

    def create():
        store = Store(tmp_path / "store")
        return create_app(store, {"demo": DemoPlugin(store)})

    return create


@pytest.fixture
def auth_client(app_factory, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_USERNAME", USERNAME)
    monkeypatch.setenv("AUTH_PASSWORD", PASSWORD)
    with TestClient(app_factory()) as client:
        yield client


def test_disabled_by_default_and_credentials_alone_do_not_enable_auth(app_factory, monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED")
    assert AuthSettings.from_env().enabled is False
    monkeypatch.setenv("AUTH_USERNAME", USERNAME)
    monkeypatch.setenv("AUTH_PASSWORD", PASSWORD)
    with TestClient(app_factory()) as client:
        for path in ("/", "/docs", "/openapi.json", "/api/library", "/static/app.js?v=test"):
            result = client.get(path, headers={"Authorization": "invalid"})
            assert result.status_code == 200, path
            assert "www-authenticate" not in result.headers
        assert "immutable" in client.get("/static/app.js?v=test").headers["cache-control"]


@pytest.mark.parametrize("value", ["true", "TRUE", " 1 ", "yes", "on"])
def test_enabled_values(value, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", value)
    monkeypatch.setenv("AUTH_USERNAME", USERNAME)
    monkeypatch.setenv("AUTH_PASSWORD", PASSWORD)
    assert AuthSettings.from_env().enabled is True


@pytest.mark.parametrize("value", ["", "false", " FALSE ", "0", "no", "off"])
def test_disabled_values_need_no_credentials(value, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", value)
    assert AuthSettings.from_env().enabled is False


@pytest.mark.parametrize("username,password", [("", PASSWORD), (USERNAME, ""), ("  ", PASSWORD),
                                               (USERNAME, "  "), ("user:name", PASSWORD),
                                               ("user\nname", PASSWORD), (USERNAME, "pass\x00word")])
def test_invalid_enabled_config_fails_without_echoing_secrets(username, password):
    with pytest.raises(ValueError) as error:
        AuthSettings(enabled=True, username=username, password=password)
    assert PASSWORD not in str(error.value)


def test_bad_switch_and_missing_credentials_stop_app_creation(app_factory, monkeypatch):
    import main

    monkeypatch.setattr(main, "AggregatorService", lambda *args: pytest.fail("invalid auth must fail before service creation"))
    monkeypatch.setenv("AUTH_ENABLED", "tru")
    with pytest.raises(ValueError, match="AUTH_ENABLED"):
        main.create_app()
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(ValueError, match="AUTH_USERNAME.*AUTH_PASSWORD"):
        main.create_app()


def test_settings_repr_contains_no_credentials():
    settings = AuthSettings(enabled=True, username=USERNAME, password=PASSWORD)
    assert USERNAME not in repr(settings) and PASSWORD not in repr(settings)


def test_all_registered_routes_and_mounts_require_auth(auth_client):
    for route in auth_client.app.routes:
        if route.path == "/health":
            continue
        path = re.sub(r"\{[^}]+\}", "demo", route.path)
        for method in getattr(route, "methods", {"GET"}):
            result = auth_client.request(method, path)
            assert result.status_code == 401, (method, path, result.text)
            assert result.headers["www-authenticate"] == 'Basic realm="Comic API", charset="UTF-8"'
            assert result.headers["cache-control"] == "no-store"
            if method != "HEAD":
                assert result.json()["error"]["code"] == "auth_required"
    assert auth_client.app.state.store.tasks() == []
    assert auth_client.app.state.store.books() == []
    assert auth_client.app.state.store.account("demo") is None


def test_only_exact_health_path_is_public(auth_client):
    result = auth_client.get("/health", headers={"Authorization": "invalid"})
    assert result.status_code == 200 and result.json() == {"status": "ok"}
    assert "www-authenticate" not in result.headers
    for path in ("/health/", "/health/anything", "/healthz", "/missing", "/static/app.js?v=test"):
        assert auth_client.get(path, follow_redirects=False).status_code == 401
    assert auth_client.post("/health").status_code == 401


@pytest.mark.parametrize("authorization", ["", "Basic", "Basic !!!", "Basic YQ==", "Basic Og==",
                                         "Basic /w==", "Basic YTpi!", "Bearer token", "Digest token"])
def test_malformed_auth_is_rejected_without_server_errors(auth_client, authorization):
    result = auth_client.get("/api/library", headers={"Authorization": authorization})
    assert result.status_code == 401
    assert result.json() == {"error": {"code": "auth_required", "message": "站点认证失败"}}


@pytest.mark.parametrize("username,password", [("wrong", PASSWORD), (USERNAME, "wrong"),
                                               ("", PASSWORD), (USERNAME, ""),
                                               (USERNAME.upper(), PASSWORD), (USERNAME, PASSWORD + " ")])
def test_incorrect_credentials_are_rejected(auth_client, username, password):
    result = auth_client.get("/api/library", headers=basic_headers(username, password))
    assert result.status_code == 401
    assert USERNAME not in result.text and PASSWORD not in result.text


def test_duplicate_authorization_headers_are_rejected(auth_client):
    header = basic_headers()["Authorization"]
    result = auth_client.get("/api/library", headers=[("Authorization", header), ("Authorization", header)])
    assert result.status_code == 401


def test_credentials_in_query_or_cookies_do_not_authenticate(auth_client):
    auth_client.cookies.set("Authorization", basic_headers()["Authorization"])
    assert auth_client.get("/api/library", params={"username": USERNAME, "password": PASSWORD}).status_code == 401


def test_valid_auth_allows_page_assets_api_and_mutations(auth_client):
    headers = basic_headers()
    headers["Authorization"] = headers["Authorization"].replace("Basic", "bAsIc", 1)
    for path in ("/", "/docs", "/redoc", "/openapi.json", "/static/app.js?v=test", "/api/library", "/api/sources"):
        result = auth_client.get(path, headers=headers)
        assert result.status_code == 200, path
        assert result.headers["cache-control"] == "no-store"
        assert "www-authenticate" not in result.headers
        assert PASSWORD not in result.text and USERNAME not in result.text
    assert auth_client.put("/api/library/demo/book", json={"category": "测试"}, headers=headers).status_code == 200
    assert len(auth_client.get("/api/library", headers=headers).json()["items"]) == 1
    # Successful Basic auth is request-scoped, not an implicit server session.
    assert auth_client.get("/api/library").status_code == 401


def test_source_login_errors_are_not_site_auth_challenges(auth_client):
    result = auth_client.get("/api/search?keyword=fail&source=demo", headers=basic_headers())
    assert result.status_code == 401 and result.json()["error"]["code"] == "login_required"
    assert "www-authenticate" not in result.headers


def test_utf8_credentials_and_password_colons_are_supported(app_factory, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_USERNAME", "读者")
    monkeypatch.setenv("AUTH_PASSWORD", " 密码:测试 ")
    with TestClient(app_factory()) as client:
        assert client.get("/api/library", headers=basic_headers("读者", " 密码:测试 ")).status_code == 200
        assert client.get("/api/library", headers=basic_headers("读者", "密码:测试")).status_code == 401


def test_images_and_pdf_downloads_require_credentials(auth_client):
    headers = basic_headers()
    path = "/api/image/proxy?source=demo&url=https://demo.invalid/1.png"
    assert auth_client.get(path).status_code == 401
    image = auth_client.get(path, headers=headers)
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "no-store"
    result = auth_client.post("/api/downloads", headers=headers,
                              json={"source": "demo", "comic_id": "book", "chapter_id": "1"})
    assert result.status_code == 202
    task = result.json()
    for _ in range(100):
        current = auth_client.app.state.store.task(task["id"])
        if current["status"] in {"completed", "failed"}:
            break
        time.sleep(.05)
    assert current["status"] == "completed", current
    path = f'/api/downloads/{task["id"]}/file'
    assert auth_client.get(path).status_code == 401
    pdf = auth_client.get(path, headers=headers)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["cache-control"] == "no-store"
    assert auth_client.get("/api/download/demo/book/1").status_code == 401


@pytest.mark.parametrize("foreign_headers", [
    {"Origin": "https://foreign.invalid"},
    {"Origin": "null"},
    {"Origin": "http://[invalid"},
    {"Origin": "http://testserver:1234"},
    {"Referer": "https://foreign.invalid/page"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site", "Origin": "http://testserver"},
])
def test_browser_cross_site_writes_and_legacy_get_download_are_blocked(auth_client, foreign_headers):
    headers = {**basic_headers(), **foreign_headers}
    for method, path in (("PUT", "/api/library/demo/book"), ("POST", "/api/jm/sync/cancel"),
                         ("GET", "/api/download/demo/book/1")):
        result = auth_client.request(method, path, json={}, headers=headers)
        assert result.status_code == 403
        assert result.json()["error"]["code"] == "cross_site_request"
        assert "www-authenticate" not in result.headers
        assert result.headers["cache-control"] == "no-store"
    assert auth_client.app.state.store.tasks() == []
    assert auth_client.app.state.store.books() == []
    assert auth_client.get("/", headers=headers).status_code == 200


@pytest.mark.parametrize("origin_headers", [
    {}, {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    {"Origin": "https://testserver"}, {"Referer": "http://testserver/?tab=library"},
])
def test_same_origin_browser_and_non_browser_clients_can_write(auth_client, origin_headers):
    result = auth_client.put("/api/library/demo/book", json={}, headers={**basic_headers(), **origin_headers})
    assert result.status_code == 200


def test_proxy_root_path_keeps_only_health_public(app_factory, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_USERNAME", USERNAME)
    monkeypatch.setenv("AUTH_PASSWORD", PASSWORD)
    with TestClient(app_factory(), root_path="/comic") as client:
        assert client.get("/comic/health").status_code == 200
        assert client.get("/comic/health/other").status_code == 401
        assert client.get("/comic/api/library").status_code == 401
        assert client.get("/comic/api/library", headers=basic_headers()).status_code == 200
        result = client.get("/comic/api/download/demo/book/1",
                            headers={**basic_headers(), "Sec-Fetch-Site": "cross-site"})
        assert result.status_code == 403


def test_password_changes_take_effect_on_new_app(app_factory, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_USERNAME", USERNAME)
    monkeypatch.setenv("AUTH_PASSWORD", PASSWORD)
    with TestClient(app_factory()) as client:
        monkeypatch.setenv("AUTH_PASSWORD", "replacement")
        assert client.get("/api/library", headers=basic_headers()).status_code == 200
    with TestClient(app_factory()) as client:
        assert client.get("/api/library", headers=basic_headers()).status_code == 401
        assert client.get("/api/library", headers=basic_headers(password="replacement")).status_code == 200


def test_websockets_cannot_bypass_auth(auth_client):
    @auth_client.app.websocket("/events")
    async def events(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("ready")
        await websocket.close()

    with pytest.raises(WebSocketDisconnect) as error:
        with auth_client.websocket_connect("/events"):
            pass
    assert error.value.code == 1008
    with pytest.raises(WebSocketDisconnect) as error:
        with auth_client.websocket_connect("/events", headers={**basic_headers(), "Origin": "https://foreign.invalid"}):
            pass
    assert error.value.code == 1008
    with auth_client.websocket_connect("/events", headers=basic_headers()) as websocket:
        assert websocket.receive_text() == "ready"
