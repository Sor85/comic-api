import asyncio
import base64
import binascii
import hashlib
import os
import secrets
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

from src.errors import ComicApiError


def validate_credentials(username, password):
    if ":" in username:
        raise ValueError("用户名不能包含冒号")
    if any(ord(char) < 32 or ord(char) == 127 for char in username + password):
        raise ValueError("站点用户名和密码不能包含控制字符")
    if len(username) > 128 or len(password) > 1024:
        raise ValueError("用户名最多 128 字符，密码最多 1024 字符")


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
        validate_credentials(self.username, self.password)

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


class AuthUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    enabled: bool = Field(strict=True)
    username: str = Field(strict=True, max_length=128)
    password: str | None = Field(default=None, strict=True, max_length=1024, repr=False)
    revision: str = Field(strict=True, min_length=1, max_length=64)


def password_hash(password: bytes, salt: str) -> str:
    return hashlib.scrypt(password, salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32).hex()


@dataclass(frozen=True)
class AuthRecord:
    enabled: bool
    username: str
    password_salt: str = field(repr=False)
    password_hash: str = field(repr=False)
    revision: str

    def __post_init__(self):
        if type(self.enabled) is not bool or any(not isinstance(value, str) for value in (
                self.username, self.password_salt, self.password_hash, self.revision)):
            raise ValueError("站点鉴权配置格式无效")
        validate_credentials(self.username, "")
        for value, length in ((self.revision, 32), (self.password_salt, 32), (self.password_hash, 64)):
            if value and (len(value) != length or any(char not in "0123456789abcdef" for char in value)):
                raise ValueError("站点鉴权配置格式无效")
        if not self.revision or bool(self.password_hash) != bool(self.password_salt):
            raise ValueError("站点鉴权配置不完整")
        if self.enabled and (not self.username.strip() or not self.password_hash):
            raise ValueError("启用站点鉴权时必须配置用户名和密码")


class AuthManager:
    """One live configuration; only salted scrypt verifiers are persisted."""

    def __init__(self, store, defaults: AuthSettings | None = None):
        self.store = store
        self._verify_lock = asyncio.Lock()
        self._verified_digest = None
        missing = object()
        saved = store.settings("site_auth", default=missing)
        self._persisted = saved is not missing
        if self._persisted:
            try:
                self._state = AuthRecord(**saved)
            except (TypeError, ValueError) as exc:
                raise ValueError("已保存的站点鉴权配置无效，服务已停止启动") from exc
        else:
            defaults = defaults or AuthSettings.from_env()
            password = defaults.password if defaults.password.strip() else ""
            validate_credentials(defaults.username, password)
            salt = secrets.token_hex(16) if password else ""
            verifier = password_hash(password.encode("utf-8"), salt) if salt else ""
            self._state = AuthRecord(defaults.enabled, defaults.username, salt, verifier, secrets.token_hex(16))
            if password:
                self._verified_digest = hashlib.sha256(password.encode("utf-8")).digest()

    @property
    def enabled(self):
        return self._state.enabled

    def status(self):
        state = self._state
        return {"enabled": state.enabled, "username": state.username,
                "password_configured": bool(state.password_hash), "revision": state.revision,
                "storage": "database" if self._persisted else "environment"}

    async def verify(self, username: bytes, password: bytes):
        state = self._state
        if not state.password_hash or len(username) > 512 or len(password) > 4096:
            return False
        username_ok = secrets.compare_digest(hashlib.sha256(username).digest(),
                                             hashlib.sha256(state.username.encode("utf-8")).digest())
        digest = hashlib.sha256(password).digest()
        if self._verified_digest is not None:
            return username_ok & secrets.compare_digest(digest, self._verified_digest)
        # Hashing is off the event loop and serialized. Cache only a successful
        # verification in memory so chapter images do not each run scrypt.
        async with self._verify_lock:
            if self._state is not state:
                return False
            if self._verified_digest is not None:
                return username_ok & secrets.compare_digest(digest, self._verified_digest)
            candidate = await asyncio.to_thread(password_hash, password, state.password_salt)
            if self._state is not state:
                return False
            valid = username_ok & secrets.compare_digest(candidate, state.password_hash)
            if valid:
                self._verified_digest = digest
            return valid

    async def save(self, values: AuthUpdate):
        state = self._state
        if values.revision != state.revision:
            raise ComicApiError("鉴权设置已变更，重新读取后可保存", 409, "auth_settings_changed")
        new_password = values.password or ""
        try:
            validate_credentials(values.username, new_password)
            if new_password and not new_password.strip():
                raise ValueError("密码不能全为空白")
            if values.enabled and (not values.username.strip() or not (new_password or state.password_hash)):
                raise ValueError("启用站点鉴权需要用户名和密码")
        except ValueError as exc:
            raise ComicApiError(str(exc), 422, "invalid_request") from exc
        salt, verifier, digest = state.password_salt, state.password_hash, self._verified_digest
        if new_password:
            salt = secrets.token_hex(16)
            verifier = await asyncio.to_thread(password_hash, new_password.encode("utf-8"), salt)
            digest = hashlib.sha256(new_password.encode("utf-8")).digest()
        if self._state is not state:
            raise ComicApiError("鉴权设置已变更，重新读取后可保存", 409, "auth_settings_changed")
        updated = AuthRecord(values.enabled, values.username, salt, verifier, secrets.token_hex(16))
        # Persist before publishing the new state; a failed write must not lock
        # users out or report an unsaved configuration as active.
        self.store.save_settings("site_auth", asdict(updated))
        self._state, self._verified_digest, self._persisted = updated, digest, True
        return {"success": True, "settings": self.status(),
                "reauthenticate": updated.enabled and (not state.enabled or updated.username != state.username
                                                       or bool(new_password))}


class BasicAuthMiddleware:
    """Protect the whole application, including mounted files and API docs."""

    def __init__(self, app, auth: AuthManager):
        self.app = app
        self.auth = auth

    async def authenticated(self, scope):
        values = Headers(scope=scope).getlist("authorization")
        if len(values) != 1:
            return False
        scheme, _, token = values[0].partition(" ")
        if scheme.lower() != "basic" or len(token) > 8192:
            return False
        try:
            decoded = base64.b64decode(token.strip(), validate=True)
        except (ValueError, binascii.Error):
            return False
        username, separator, password = decoded.partition(b":")
        return bool(separator) and await self.auth.verify(username, password)

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
        protected = self.auth.enabled
        if protected and not await self.authenticated(scope):
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
            if message["type"] == "http.response.start" and (protected or self.auth.enabled):
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_private)
