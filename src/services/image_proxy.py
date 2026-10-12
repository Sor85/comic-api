"""Image proxying: SSRF guard, per-request concurrency bound and an on-disk cache."""
import asyncio
import hashlib
import time
import urllib.parse

from src.errors import ComicApiError
from src.services.image_network import image_target

_IMAGE_REDIRECTS = (301, 302, 303, 307, 308)
_MAX_IMAGE_REDIRECTS = 5


def ensure_public_url(url):
    """Return the validated connection target; callers must pass it to the transport."""
    return image_target(url)


def _request_image(client, url, target, retries):
    """请求单个已校验目标，对临时网络故障重试并轮换公网 IP。"""
    # JM 图片服务对 Referer 较敏感，使用站点根域而非图片自身 URL
    parsed = urllib.parse.urlparse(url)
    headers = {
        "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        "User-Agent": client.ua,
    }

    last_err = None
    for attempt in range(max(1, retries)):
        try:
            res = client.request("GET", url, headers=headers, timeout=30, allow_redirects=False,
                                 _image_target=target.retry(attempt) if target else None)
            if res.status_code in _IMAGE_REDIRECTS or (res.status_code == 200 and res.content):
                return res
            status = res.status_code
            last_err = ComicApiError(f"图片下载失败 (HTTP {status})",
                                   status if status in (401, 403, 404, 429) else 502,
                                   "image_download_failed")
            if status in (401, 403, 404):
                break
        except Exception as e:
            last_err = e

        # 退避后重试（0.5s, 1s, ...）
        if attempt < retries - 1:
            time.sleep(0.5 * (attempt + 1))

    raise last_err or Exception(f"Failed to download image from {url}")


def download_image(client, url: str, retries: int = 3) -> bytes:
    """下载图片，逐跳校验公网地址并固定连接目标，不让传输层自动跳转。"""
    visited = set()
    for redirects in range(_MAX_IMAGE_REDIRECTS + 1):
        target = ensure_public_url(url)
        url = target.url if target else url
        if url in visited:
            raise ComicApiError("图片重定向存在循环", 502, "image_download_failed")
        visited.add(url)
        res = _request_image(client, url, target, retries)
        if res.status_code == 200:
            return res.content
        if redirects == _MAX_IMAGE_REDIRECTS:
            raise ComicApiError("图片重定向次数过多", 502, "image_download_failed")
        location = res.headers.get("Location")
        if not isinstance(location, str) or not location:
            raise ComicApiError("图片重定向缺少目标地址", 502, "image_download_failed")
        try:
            url = urllib.parse.urljoin(url, location)
        except ValueError as exc:
            raise ComicApiError("图片重定向地址无效", 502, "image_download_failed") from exc


class ImageProxy:
    def __init__(self, store):
        self.store = store
        self._slots = asyncio.Semaphore(8)

    async def image(self, plugin, source, url, chapter_id="") -> bytes:
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=10)
        except asyncio.TimeoutError as exc:
            raise ComicApiError("图片请求繁忙", 429, "busy", source) from exc
        # Do not reuse entries fetched before destination pinning / redirect checks.
        key = hashlib.sha256(f"public-v1:{source}:{chapter_id}:{url}".encode()).hexdigest()[:32]
        cache = self.store.root / "image_cache"
        path = cache / f"{key}.img"

        def process():
            if path.is_file():
                return path.read_bytes()
            data = plugin.transform_image(download_image(plugin.client, url), chapter_id, url)
            try:
                cache.mkdir(exist_ok=True)
                path.write_bytes(data)
                self._trim(cache)
            except OSError:
                pass
            return data

        task = asyncio.create_task(asyncio.to_thread(process))
        task.add_done_callback(
            lambda done: (self._slots.release(), done.exception() if not done.cancelled() else None))
        return await asyncio.shield(task)

    @staticmethod
    def _trim(cache, keep=2000):
        files = sorted(cache.iterdir(), key=lambda item: item.stat().st_mtime)
        for stale in files[:-keep]:
            try:
                stale.unlink()
            except OSError:
                pass
