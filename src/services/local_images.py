"""Local image output for JM sync. Paths are confined to administrator-owned roots."""
import hashlib
import io
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

from src.errors import ComicApiError, DownloadCancelled
from src.services.image_proxy import ensure_public_url


DIRECTORY_RULES = {
    "id": "漫画 ID / 章节 ID / 图片",
    "title": "漫画标题 [ID] / 章节序号 - 标题 [ID] / 图片",
    "author": "作者 / 漫画标题 [ID] / 章节序号 - 标题 [ID] / 图片",
}
FORMATS = {"original": "保留图片格式", "jpeg": "JPEG", "png": "PNG", "webp": "WebP"}
MAX_IMAGE_BYTES = 64 * 1024 * 1024


def safe_name(value, fallback="未命名"):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", str(value or ""))
    name = name.encode("utf-8")[:100].decode("utf-8", errors="ignore").strip(" .") or fallback
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name


def numeric_id(value):
    value = str(value)
    if not re.fullmatch(r"[0-9]{1,20}", value):
        raise ComicApiError("禁漫返回了无效编号", 502, "invalid_response", "jm")
    return value


class DownloadPaths:
    def __init__(self, store):
        configured = os.environ.get("JM_DOWNLOAD_ROOTS", "")
        self.roots = [Path(p.strip()).expanduser().resolve() for p in configured.split(os.pathsep) if p.strip()]
        if not self.roots:
            self.roots = [(store.root / "jm-favorites").resolve()]

    def resolve(self, value):
        value = str(value).replace("\\", "/")
        path = Path(value).expanduser()
        if not path.is_absolute() or ".." in path.parts:
            raise ComicApiError("下载目录必须是允许范围内的服务器绝对路径", 422, "invalid_directory")
        # Refuse symlinks even when their current target is within a root.
        for root in self.roots:
            if path.is_relative_to(root):
                current = root
                for part in path.relative_to(root).parts:
                    current /= part
                    if current.is_symlink():
                        raise ComicApiError("下载目录不允许包含符号链接", 422, "invalid_directory")
                resolved = path.resolve()
                if resolved.is_relative_to(root):
                    return resolved
        raise ComicApiError("目录不在 JM_DOWNLOAD_ROOTS 允许范围内", 422, "invalid_directory")

    def browse(self, value=""):
        current = self.resolve(value or self.roots[0])
        if current.exists() and not current.is_dir():
            raise ComicApiError("目标不是目录", 422, "invalid_directory")
        children = []
        try:
            if current.is_dir():
                for child in current.iterdir():
                    if not child.name.startswith(".") and not child.is_symlink() and child.is_dir():
                        children.append({"name": child.name, "path": str(child)})
            children.sort(key=lambda item: item["name"].casefold())
        except OSError as exc:
            raise ComicApiError("目录不可读取", 403, "directory_unavailable") from exc
        parent = str(current.parent) if any(current.parent.is_relative_to(root) for root in self.roots) else None
        return {"roots": [str(root) for root in self.roots], "path": str(current), "parent": parent,
                "directories": children[:500], "truncated": len(children) > 500}

    def prepare(self, value):
        path = self.resolve(value)
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=path):
                pass
        except OSError as exc:
            raise ComicApiError("下载目录不可写，需检查路径、挂载和权限", 422, "directory_unavailable") from exc
        return path

    def chapter(self, settings, book, chapter, index):
        album_id, chapter_id = numeric_id(book["id"]), numeric_id(chapter["id"])
        root = self.resolve(settings["download_dir"])
        if settings["directory_rule"] == "id":
            return self.resolve(root / album_id / chapter_id)
        album = f"{safe_name(book['title'])} [{album_id}]"
        episode = f"{index:03d} - {safe_name(chapter.get('name'))} [{chapter_id}]"
        if settings["directory_rule"] == "author":
            root /= safe_name(book.get("author"), "未知作者")
        return self.resolve(root / album / episode)


def encode_image(data, image_format, quality):
    """Preserve animated originals; explicit conversion deliberately uses the first frame."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            source_format = image.format
            image.load()
            if image_format == "original":
                extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "BMP": "bmp", "TIFF": "tiff", "AVIF": "avif"}.get(source_format)
                if not extension:
                    raise ValueError("Unsupported image format")
                return data, extension
            output = io.BytesIO()
            if image_format == "jpeg":
                rgba = image.convert("RGBA")
                canvas = Image.new("RGB", image.size, "white")
                canvas.paste(rgba, mask=rgba.getchannel("A"))
                canvas.save(output, "JPEG", quality=quality)
            elif image_format == "webp":
                image.convert("RGBA").save(output, "WEBP", quality=quality)
            else:
                image.convert("RGBA" if "A" in image.getbands() else "RGB").save(output, "PNG")
            return output.getvalue(), "jpg" if image_format == "jpeg" else image_format
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ComicApiError("图片无法解码或转换", 502, "invalid_image", "jm") from exc


def receive_image(plugin, url, chapter_id, settings, signal):
    """One sync worker + libcurl receive limit bounds aggregate sync traffic, not reader traffic."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        raise ComicApiError("图片地址无效", 502, "invalid_image", "jm")
    ensure_public_url(url)
    rate = settings["speed_limit_kbps"] * 1024
    last_error = None
    for attempt in range(settings["retries"] + 1):
        if signal.is_set():
            raise DownloadCancelled()
        output = io.BytesIO()
        started = time.monotonic()
        try:
            # In curl_cffi stream mode this is a connect + low-speed timeout, not a
            # total deadline that would break large images at a low bandwidth limit.
            with plugin.client.stream(
                "GET", url, headers={"Referer": f"{parsed.scheme}://{parsed.netloc}/"},
                allow_redirects=False, max_recv_speed=rate, timeout=(10, 20)) as response:
                if response.status_code != 200:
                    raise ComicApiError(f"图片下载失败 (HTTP {response.status_code})", 502, "image_download_failed", "jm")
                for chunk in response.iter_content():
                    if signal.is_set():
                        raise DownloadCancelled()
                    if output.tell() + len(chunk) > MAX_IMAGE_BYTES:
                        raise ComicApiError("单张图片超过 64 MiB 限制", 502, "image_too_large", "jm")
                    output.write(chunk)
            if signal.is_set():
                raise DownloadCancelled()
            data = output.getvalue()
            # libcurl allows short-transfer bursts; pacing also bounds many small images.
            if rate and signal.wait(max(0, len(data) / rate - (time.monotonic() - started))):
                raise DownloadCancelled()
            transformed = plugin.transform_image(data, chapter_id, url)
            converted, extension = encode_image(transformed, settings["image_format"], settings["quality"])
            return converted, extension, len(data)
        except Exception as exc:
            if signal.is_set() or isinstance(exc, DownloadCancelled):
                raise DownloadCancelled() from exc
            if isinstance(exc, ComicApiError) and exc.code in ("image_too_large", "invalid_image"):
                raise
            last_error = exc
            if attempt < settings["retries"] and signal.wait(min(2 ** attempt, 8)):
                raise DownloadCancelled()
    raise last_error


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_atomic(path, data, replace=False):
    """Never overwrite an untracked file; only our temporary file is removed on failure."""
    fd, temporary = tempfile.mkstemp(prefix=".comic-sync-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
