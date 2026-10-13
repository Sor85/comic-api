"""Source dispatch, search aggregation and ranking. Image proxy and PDF building
live in src/services/image_proxy.py and src/services/pdf.py."""
import asyncio
import unicodedata
from difflib import SequenceMatcher
from typing import List, Dict, Any

from src.errors import ComicApiError
from src.services.image_proxy import ImageProxy
from src.services.pdf import build_chapter_pdf
from src.sources import load_sources
from src.storage import Store


class AggregatorService:
    def __init__(self, store=None, sources=None):
        self.store = store or Store()
        self.sources = load_sources(self.store) if sources is None else sources
        self._slots = {key: asyncio.Semaphore(2) for key in self.sources}
        self._image_proxy = ImageProxy(self.store)
        self._enabled = self.store.settings("source_states")

    def is_enabled(self, source):
        return source in self.sources and self._enabled.get(source) is not False

    def source(self, source, *, allow_disabled=False):
        if source not in self.sources:
            raise ComicApiError("未知图源", 404, "unknown_source", source)
        if not allow_disabled and not self.is_enabled(source):
            raise ComicApiError("图源已关闭，可在图源管理中重新启用", 409, "source_disabled", source)
        return self.sources[source]

    def manifest(self, source):
        return dict(self.source(source, allow_disabled=True).manifest(), enabled=self.is_enabled(source))

    def set_enabled(self, source, enabled):
        self.source(source, allow_disabled=True)
        if not isinstance(enabled, bool):
            raise ComicApiError("图源开关必须为布尔值", 422, "invalid_request", source)
        states = {**self._enabled, source: enabled}
        self.store.save_settings("source_states", states)
        self._enabled = states

    async def call(self, source, method, *args, **kwargs):
        # Account removal remains available without re-enabling upstream requests.
        allow_disabled = method == "logout"
        plugin = self.source(source, allow_disabled=allow_disabled)
        operation = getattr(plugin, method, None)
        if not callable(operation):
            raise ComicApiError("图源不支持此功能", 400, "unsupported", source)
        slot = self._slots[source]
        try:
            await asyncio.wait_for(slot.acquire(), timeout=10)
        except asyncio.TimeoutError as exc:
            raise ComicApiError("图源繁忙，请稍后重试", 429, "busy", source) from exc
        def invoke():
            # A request may have waited for a source slot or an executor thread.
            self.source(source, allow_disabled=allow_disabled)
            return operation(*args, **kwargs)

        # Cancellation must not release the permit while the underlying thread is running.
        task = asyncio.create_task(asyncio.to_thread(invoke))
        task.add_done_callback(lambda completed: (slot.release(), completed.exception() if not completed.cancelled() else None))
        try:
            return await asyncio.shield(task)
        except ComicApiError as exc:
            exc.source = source
            raise
        except Exception as exc:
            raise ComicApiError("图源响应异常，请稍后重试", 502, "upstream_error", source) from exc

    @staticmethod
    def normalize_title(value):
        return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())

    def rank_results(self, keyword, items):
        query = self.normalize_title(keyword)

        def score(item):
            title = self.normalize_title(item.get('title', ''))
            if not query or not title:
                return (0, 0)
            tier = 3 if query == title else 2 if query in title else 1
            return (tier, SequenceMatcher(None, query, title, autojunk=False).ratio())
        # Stable ties preserve upstream order; identical titles across sources stay selectable.
        return sorted(items, key=score, reverse=True)

    async def search_source(self, source, keyword, page=1, sort="", mode="keyword", tag_value="", category=""):
        plugin = self.source(source)
        capability = "tag_search" if mode == "tag" else "search"
        if capability not in plugin.capabilities:
            raise ComicApiError("图源不支持此搜索方式", 422, "unsupported", source)
        sorts = plugin.tag_sorts if mode == "tag" else plugin.search_sorts
        if sort and sort not in [item["value"] for item in sorts]:
            raise ComicApiError("图源不支持此搜索排序", 422, "invalid_request", source)
        if category and "search_categories" not in plugin.capabilities:
            raise ComicApiError("图源不支持搜索分类筛选", 422, "invalid_request", source)
        # Only send optional arguments when used, preserving older plugin signatures.
        kwargs = {key: value for key, value in {"sort": sort, "category": category}.items() if value}
        if mode == "tag" and tag_value:
            kwargs["tag_value"] = tag_value
        result = await self.call(source, "search_tag" if mode == "tag" else "search", keyword, page, **kwargs)
        items = [dict(item, source=source) for item in result]
        return items if mode == "tag" else self.rank_results(keyword, items)

    async def aggregate_search(self, keyword: str, page: int = 1, mode: str = "keyword") -> Dict[str, Any]:
        """Search registered sources independently and retain partial failures."""
        capability = "tag_search" if mode == "tag" else "search"
        keys = [key for key, plugin in self.sources.items() if self.is_enabled(key) and capability in plugin.capabilities]
        results = await asyncio.gather(*(self.search_source(key, keyword, page, mode=mode) for key in keys), return_exceptions=True)
        all_results, errors, candidates = {}, {}, []
        for key, result in zip(keys, results):
            if not self.is_enabled(key):
                continue
            if isinstance(result, Exception):
                errors[key] = result.payload() if isinstance(result, ComicApiError) else {"message": "图源异常"}
                all_results[key] = []
            else:
                all_results[key] = [dict(item, source=key) for item in result]
                candidates.extend(all_results[key])
        if mode == "keyword":
            candidates = self.rank_results(keyword, candidates)
        return {"keyword": keyword, "best_match": candidates[0] if candidates else None,
                "items": candidates, "all_results": all_results, "errors": errors}

    async def get_comic_detail(self, source: str, comic_id: str) -> Dict[str, Any]:
        """获取指定渠道下的漫画详情与章节"""
        result = await self.call(source, "detail", comic_id)
        if not result or not result.get("title"):
            raise ComicApiError("漫画不存在或图源未返回有效详情", 404, "not_found", source)
        return dict(result, source=source, id=comic_id)

    async def get_chapter_images(self, source: str, comic_id: str, chapter_id: str) -> List[str]:
        """获取指定渠道、漫画和章节下的图片"""
        return await self.call(source, "pages", comic_id, chapter_id)

    async def image(self, source, url, chapter_id=""):
        return await self._image_proxy.image(self.source(source), source, url, chapter_id,
                                             check=lambda: self.source(source))

    async def download_chapter_pdf(self, source: str, comic_id: str, chapter_id: str,
                                   concurrency: int = 4, password: str = "",
                                   progress=None, cancelled=None) -> str:
        """下载章节并打包成自适应压缩的 PDF，返回本地临时 PDF 路径"""
        return await build_chapter_pdf(self, source, comic_id, chapter_id,
                                       concurrency=concurrency, password=password,
                                       progress=progress,
                                       cancelled=lambda: not self.is_enabled(source) or bool(cancelled and cancelled()))
