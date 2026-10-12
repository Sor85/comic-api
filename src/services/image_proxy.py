"""Image proxying: SSRF guard, per-request concurrency bound and an on-disk cache."""
import asyncio
import hashlib
import time
import urllib.parse

from src.errors import ComicApiError
from src.services.image_network import image_target


def ensure_public_url(url):
    """Return the validated connection target; callers must pass it to the transport."""
    return image_target(url)


def download_image(client, url: str, retries: int = 3) -> bytes:
    """使用 Client 自身的 BaseClient.request 来下载图片二进制。

    JM 图片 CDN 节点不稳定，单张图常随机返回 403/连接重置，
    因此加入重试+退避，避免单次失败导致整张图裂掉。
    """
    # JM 图片服务对 Referer 较敏感，使用站点根域而非图片自身 URL
    target = ensure_public_url(url)
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
            if res.status_code == 200 and res.content:
                return res.content
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
