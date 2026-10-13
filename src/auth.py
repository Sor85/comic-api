import base64
import binascii
import hashlib
import os
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool = False
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)

    def __post_init__(self):
        if not self.enabled:
            return
        if not self.username.strip() or not self.password.strip():
            raise ValueError("启用站点鉴权时必须配置非空的 AUTH_USERNAME 和 AUTH_PASSWORD")
        if ":" in self.username:
            raise ValueError("AUTH_USERNAME 不能包含冒号")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.username + self.password):
            raise ValueError("站点用户名和密码不能包含控制字符")

    @classmethod
    def from_env(cls):
        enabled = os.getenv("AUTH_ENABLED", "false").strip().lower()
        if enabled not in {"", "0", "false", "no", "off", "1", "true", "yes", "on"}:
            raise ValueError("AUTH_ENABLED 必须为 true 或 false（也支持 1/0、yes/no、on/off）")
        return cls(
            enabled=enabled in {"1", "true", "yes", "on"},
            username=os.getenv("AUTH_USERNAME", ""),
            password=os.getenv("AUTH_PASSWORD", ""),
        )


class BasicAuthMiddleware:
    """Protect the whole application, including mounted files and API docs."""

    def __init__(self, app, settings: AuthSettings):
        self.app = app
        self.username_digest = hashlib.sha256(settings.username.encode("utf-8")).digest()
        self.password_digest = hashlib.sha256(settings.password.encode("utf-8")).digest()

    def authenticated(self, scope):
        values = Headers(scope=scope).getlist("authorization")
        if len(values) != 1:
            return False
        scheme, _, token = values[0].partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            decoded = base64.b64decode(token.strip(), validate=True)
        except (ValueError, binascii.Error):
            return False
        username, separator, password = decoded.partition(b":")
        if not separator:
            return False
        # Compare fixed-length digests, and always check both fields.
        username_ok = secrets.compare_digest(hashlib.sha256(username).digest(), self.username_digest)
        password_ok = secrets.compare_digest(hashlib.sha256(password).digest(), self.password_digest)
        return username_ok & password_ok

    @staticmethod
    def cross_site_request(scope):
        headers = Headers(scope=scope)
        if headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            return True
        # Basic credentials are ambient browser credentials. Reject foreign
        # origins for writes, including browsers without Fetch Metadata support.
        origin = headers.get("origin") or headers.get("referer")
        if origin is None:
            return False
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return True
        # Compare authority rather than scheme to support TLS-terminating proxies.
        return parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != headers.get("host", "").lower()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        path = scope["path"].removeprefix(scope.get("root_path", ""))
        # Only the minimal liveness response is public; never exempt a prefix.
        if scope["type"] == "http" and path == "/health" and scope["method"] in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        if not self.authenticated(scope):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                response = JSONResponse(
                    {"error": {"code": "auth_required", "message": "站点认证失败"}},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": 'Basic realm="Comic API", charset="UTF-8"',
                        "Cache-Control": "no-store",
                    },
                )
                await response(scope, receive, send)
            return

        writes_data = (scope["type"] == "websocket" or scope["method"] not in {"GET", "HEAD", "OPTIONS"}
                       or path.startswith("/api/download/"))
        if writes_data and self.cross_site_request(scope):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                response = JSONResponse(
                    {"error": {"code": "cross_site_request", "message": "跨站操作已拒绝"}},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
            return

        async def send_private(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_private)
