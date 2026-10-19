"""Local image output for library sync. Paths are confined to administrator-owned roots."""
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
    "id": "漫画 ID / 章节 ID",
    "title": "漫画标题 [ID] / 章节序号 - 章节标题 [ID]",
    "title_plain": "漫画标题 / 第N話 章节标题",
    "author": "作者 / 漫画标题 [ID] / 章节序号 - 章节标题 [ID]",
}
FORMATS = {"original": "保留图片格式", "jpeg": "JPEG", "png": "PNG", "webp": "WebP"}
MAX_IMAGE_BYTES = 64 * 1024 * 1024
# Common Linux filesystems (ext4, ZFS, btrfs) cap a single name at 255 bytes.
MAX_NAME_BYTES = 255
MOUNTINFO = Path("/proc/self/mountinfo")
CONTAINER_MARKERS = (Path("/.dockerenv"), Path("/run/.containerenv"))
# Kernel and runtime filesystems never hold user-mapped storage.
VIRTUAL_FILESYSTEMS = {"proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "mqueue", "cgroup", "cgroup2", "overlay",
                       "securityfs", "pstore", "debugfs", "tracefs", "bpf", "hugetlbfs", "fusectl", "configfs",
                       "binfmt_misc", "autofs", "nsfs", "ramfs"}
SYSTEM_PREFIXES = (Path("/proc"), Path("/sys"), Path("/dev"), Path("/run"), Path("/etc"), Path("/usr"), Path("/tmp"))


def safe_name(value, fallback="未命名", max_bytes=100):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", str(value or ""))
    if max_bytes is not None:
        name = name.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    name = name.strip(" .") or fallback
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name


def plain_chapter_name(chapter, index):
    title = str(chapter.get("name") or "未命名").strip()
    # Do not repeat the episode prefix when it is already part of the upstream title.
    prefix = re.match(r"^第\s*([0-9]+)\s*[話话]\s*(.*)$", title, re.DOTALL)
    if prefix and int(prefix[1]) == index:
        title = prefix[2].strip()
    return safe_name(f"第{index}話 {title}", max_bytes=MAX_NAME_BYTES)


def mapped_directories(data_dir):
    """Directories mapped into this container (Docker/Podman volumes), excluding the data directory.

    Outside a container nothing is returned, so a plain host never exposes its own mounts.
    """
    if not any(marker.exists() for marker in CONTAINER_MARKERS):
        return []
    try:
        lines = MOUNTINFO.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
    except OSError:
        return []
    data_dir = Path(data_dir).resolve()
    result = []
    for line in lines:
        fields = line.split(" ")
        try:
            fstype = fields[fields.index("-") + 1]
        except (ValueError, IndexError):
            continue
        # mountinfo escapes space, tab, newline and backslash as octal.
        path = Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4]))
        if (fstype in VIRTUAL_FILESYSTEMS or path == Path("/") or any(path.is_relative_to(prefix) for prefix in SYSTEM_PREFIXES)
                or path.is_relative_to(data_dir) or data_dir.is_relative_to(path) or not path.is_dir() or path.is_symlink()):
            continue
        if path not in result:
            result.append(path)
    return result


class DownloadPaths:
    """Download roots come from an administrator-controlled environment variable.

    Without it, directories mapped into the container come first (the first is the default save location),
    followed by ``default`` under the data directory.
    """

    def __init__(self, store, env, default, comic_id=str, chapter_id=str):
        self.env, self.comic_id, self.chapter_id = env, comic_id, chapter_id
        configured = os.environ.get(env, "")
        self.roots = [Path(p.strip()).expanduser().resolve() for p in configured.split(os.pathsep) if p.strip()]
        if not self.roots:
            self.roots = [*mapped_directories(store.root), (store.root / default).resolve()]

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
        raise ComicApiError(f"目录不在 {self.env} 允许范围内", 422, "invalid_directory")

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

    def album(self, settings, book):
        album_id = self.comic_id(book["id"])
        root = self.resolve(settings["download_dir"])
        rule = settings["directory_rule"]
        if rule == "id":
            album = album_id
        elif rule == "title_plain":
            album = safe_name(book["title"], max_bytes=MAX_NAME_BYTES)
        else:
            album = f"{safe_name(book['title'])} [{album_id}]"
            if rule == "author":
                root /= safe_name(book.get("author"), "未知作者")
        return self.resolve(root / album)

    def chapter(self, settings, book, chapter, index):
        chapter_id = self.chapter_id(chapter["id"])
        album = self.album(settings, book)
        rule = settings["directory_rule"]
        if rule == "id":
            episode = chapter_id
        elif rule == "title_plain":
            episode = plain_chapter_name(chapter, index)
        else:
            episode = f"{index:03d} - {safe_name(chapter.get('name'))} [{chapter_id}]"
        return self.resolve(album / episode)


def encode_image(data, image_format, quality, source=""):
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
        raise ComicApiError("图片无法解码或转换", 502, "invalid_image", source) from exc


def receive_image(plugin, url, chapter_id, settings, signal, source=""):
    """One sync worker + libcurl receive limit bounds aggregate sync traffic, not reader traffic."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        raise ComicApiError("图片地址无效", 502, "invalid_image", source)
    target = ensure_public_url(url)
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
                allow_redirects=False, max_recv_speed=rate, timeout=(10, 20),
                _image_target=target.retry(attempt) if target else None) as response:
                if response.status_code != 200:
                    raise ComicApiError(f"图片下载失败 (HTTP {response.status_code})", 502, "image_download_failed", source)
                for chunk in response.iter_content():
                    if signal.is_set():
                        raise DownloadCancelled()
                    if output.tell() + len(chunk) > MAX_IMAGE_BYTES:
                        raise ComicApiError("单张图片超过 64 MiB 限制", 502, "image_too_large", source)
                    output.write(chunk)
            if signal.is_set():
                raise DownloadCancelled()
            data = output.getvalue()
            # libcurl allows short-transfer bursts; pacing also bounds many small images.
            if rate and signal.wait(max(0, len(data) / rate - (time.monotonic() - started))):
                raise DownloadCancelled()
            transformed = plugin.transform_image(data, chapter_id, url)
            converted, extension = encode_image(transformed, settings["image_format"], settings["quality"], source)
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


def file_digest(path, signal=None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if signal is not None and signal.is_set():
                raise DownloadCancelled()
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
