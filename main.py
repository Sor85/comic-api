import asyncio
import hashlib
import io
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

from src.auth import AuthManager, AuthUpdate, BasicAuthMiddleware
from src.errors import ComicApiError
from src.services.aggregator import AggregatorService
from src.services import cron
from src.services.bika_sync import BikaSync
from src.services.downloads import DownloadManager, pdf_password_for
from src.services.favorites_library_sync import FavoritesLibrarySettings, FavoritesLibrarySync
from src.services.jm_sync import JmSync
from src.services.library_image_sync import LibraryImageSettings, LibraryImageSync
from src.services.search_dedup import search_result_payload
from src.storage import Store

load_dotenv()
ROOT = Path(__file__).resolve().parent
WEB_STATIC = ROOT / "src/web/static"
INDEX_HTML = ROOT / "src/web/templates/index.html"


def bundled_asset_version() -> str:
    """Content hash of the web bundle; changes whenever app.css/app.js change.

    The template links assets as ``?v=<token>``. Deriving the token from file
    contents (instead of a hand-bumped integer) is what keeps returning
    browsers from serving a stale bundle after a deploy.
    """
    digest = hashlib.sha256()
    for name in sorted(p.name for p in WEB_STATIC.glob("*") if p.is_file()):
        digest.update(name.encode("utf-8"))
        digest.update((WEB_STATIC / name).read_bytes())
    return digest.hexdigest()[:12]


def versioned_index_html() -> str:
    token = bundled_asset_version()
    return re.sub(r"\?v=[^\"'&]+", f"?v={token}", INDEX_HTML.read_text(encoding="utf-8"))


class VersionedStaticFiles(StaticFiles):
    """Immutable caching for hash-versioned URLs, revalidation for the rest."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            if scope.get("query_string"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "no-cache"
        return response


class SourceStateInput(BaseModel):
    enabled: bool = Field(strict=True)


class BookInput(BaseModel):
    category: str = Field(default="", max_length=100)


class TaskInput(BaseModel):
    source: str = Field(min_length=1, max_length=64)
    comic_id: str = Field(min_length=1, max_length=200)
    chapter_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=500)
    chapter: str = Field(default="", max_length=500)


class ReadingInput(BaseModel):
    title: str = Field(default="", max_length=500)
    cover: str = Field(default="", max_length=2048)
    author: str = Field(default="", max_length=500)
    chapter_id: str = Field(min_length=1, max_length=200)
    chapter_name: str = Field(default="", max_length=500)
    page: int = Field(ge=0, le=100000)
    total: int = Field(default=0, ge=0, le=100000)


class ReadingImportComic(ReadingInput):
    source: str = Field(min_length=1, max_length=64)
    comic_id: str = Field(min_length=1, max_length=200)
    in_history: bool = True
    updated_at: int = Field(ge=0, le=10**14)


class ReadingImportChapter(BaseModel):
    source: str = Field(min_length=1, max_length=64)
    comic_id: str = Field(min_length=1, max_length=200)
    chapter_id: str = Field(min_length=1, max_length=200)
    page: int = Field(ge=0, le=100000)
    updated_at: int = Field(ge=0, le=10**14)


class ReadingImport(BaseModel):
    comics: list[ReadingImportComic] = Field(default_factory=list, max_length=2000)
    chapters: list[ReadingImportChapter] = Field(default_factory=list, max_length=20000)


class SyncInput(BaseModel):
    album_ids: list[str] | None = Field(default=None, min_length=1, max_length=200)


def create_app(store=None, sources=None):
    store = store or Store()
    auth = AuthManager(store)
    service = AggregatorService(store, sources)
    downloads = DownloadManager(store, service)
    jm_sync = JmSync(store, service)
    bika_sync = BikaSync(store, service)
    image_syncs = {"jm": jm_sync, "bika": bika_sync}
    library_sync = FavoritesLibrarySync(store, service)
    library_images = LibraryImageSync(store, service, image_syncs)

    @asynccontextmanager
    async def lifespan(app):
        await downloads.start()
        for sync in image_syncs.values():
            await sync.start()
        await library_sync.start()
        await library_images.start()
        try:
            yield
        finally:
            await library_images.stop()
            await library_sync.stop()
            for sync in image_syncs.values():
                await sync.stop()
            await downloads.stop()

    app = FastAPI(title="Comic · 图源与书库", version="2.0.0", lifespan=lifespan)
    app.add_middleware(BasicAuthMiddleware, auth=auth)
    app.state.auth = auth
    app.state.store, app.state.service, app.state.downloads = store, service, downloads
    app.state.jm_sync, app.state.bika_sync, app.state.library_sync = jm_sync, bika_sync, library_sync
    app.state.library_images = library_images
    app.mount("/static", VersionedStaticFiles(directory=WEB_STATIC), name="static")

    @app.exception_handler(ComicApiError)
    async def source_error(request, exc):
        return JSONResponse({"error": exc.payload()}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return JSONResponse({"error": {"code": "invalid_request", "message": "请求参数无效"}}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        logging.getLogger(__name__).exception("Request failed: %s", request.url.path)
        return JSONResponse({"error": {"code": "internal_error", "message": "服务内部错误"}}, status_code=500)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/")
    async def home():
        return HTMLResponse(versioned_index_html(), headers={"Cache-Control": "no-cache"})

    @app.get("/api/settings/auth")
    async def auth_settings():
        return JSONResponse(auth.status(), headers={"Cache-Control": "no-store"})

    @app.put("/api/settings/auth")
    async def save_auth_settings(values: AuthUpdate):
        return JSONResponse(await auth.save(values), headers={"Cache-Control": "no-store"})

    @app.get("/api/sources")
    async def sources_list():
        return JSONResponse({"sources": [service.manifest(source) for source in service.sources]},
                            headers={"Cache-Control": "no-store"})

    @app.put("/api/sources/{source}/state")
    async def set_source_state(source: str, values: SourceStateInput):
        service.set_enabled(source, values.enabled)
        if not values.enabled:
            downloads.cancel_source(source)
        if source in image_syncs:
            image_syncs[source].source_changed()
        library_sync.source_changed(source)
        library_images.source_changed(source)
        return {"success": True, "source": service.manifest(source)}

    @app.post("/api/sources/{source}/login")
    @app.post("/api/{source}/login", include_in_schema=False)
    async def login(source: str, values: dict[str, str]):
        plugin = service.source(source)
        if "login" not in plugin.capabilities:
            raise ComicApiError("该图源不提供登录", 400, "unsupported", source)
        if any(not values.get(field["name"], "").strip() for field in plugin.login_fields):
            raise ComicApiError("请填写全部登录字段", 422, "invalid_request", source)
        sync = image_syncs.get(source)
        if sync:
            sync.guard_account_change()
        try:
            async with library_sync.account_change(source):
                await service.call(source, "login", values)
                if sync:
                    sync.account_changed()
        finally:
            if sync:
                sync.account_changing = False
        return {"success": True, "account": plugin.account_status()}

    @app.put("/api/sources/{source}/settings")
    async def save_source_settings(source: str, values: dict[str, str]):
        plugin = service.source(source, allow_disabled=True)
        if not plugin.settings_fields:
            raise ComicApiError("该图源暂无可修改的服务器设置", 400, "unsupported", source)
        # Serialize updates on the event loop. Existing requests retain their adapter.
        service.sources[source] = plugin.save_settings(values)
        return {"success": True, "source": service.manifest(source)}

    @app.delete("/api/sources/{source}/account")
    async def logout(source: str):
        if "login" not in service.source(source, allow_disabled=True).capabilities:
            raise ComicApiError("该图源不提供登录", 400, "unsupported", source)
        sync = image_syncs.get(source)
        if sync:
            sync.guard_account_change()
        try:
            async with library_sync.account_change(source):
                await service.call(source, "logout")
                if sync:
                    sync.account_changed()
        finally:
            if sync:
                sync.account_changing = False
        return {"success": True}

    @app.get("/api/sources/{source}/library-sync")
    async def library_sync_status(source: str):
        return JSONResponse(library_sync.status(source), headers={"Cache-Control": "no-store"})

    @app.put("/api/sources/{source}/library-sync")
    async def save_library_sync_settings(source: str, values: FavoritesLibrarySettings):
        return library_sync.save_settings(source, values)

    @app.post("/api/sources/{source}/library-sync/run", status_code=202)
    async def run_library_sync(source: str):
        return library_sync.launch(source)

    @app.get("/api/jm/favorites")
    async def jm_favorites(page: int = Query(1, ge=1, le=1000),
                           folder_id: str = Query("0", pattern=r"^[0-9]{1,20}$"),
                           order: str = Query("mr", pattern=r"^(mr|mp)$")):
        jm_sync.require_account()
        return await service.call("jm", "favorites", page=page, folder_id=folder_id, order=order)

    @app.get("/api/jm/favorites/{comic_id}")
    async def jm_favorite_status(comic_id: str):
        if not re.fullmatch(r"[0-9]{1,20}", comic_id) or int(comic_id) < 1:
            raise ComicApiError("漫画编号无效", 422, "invalid_request", "jm")
        jm_sync.require_account()
        owner = store.account_owner((store.account("jm") or {}).get("account"))
        if not owner:
            raise ComicApiError("禁漫收藏需要绑定账号", 401, "login_required", "jm")
        comic_id = str(int(comic_id))
        favorited = await asyncio.to_thread(store.jm_favorite_status, owner, comic_id)
        return JSONResponse({"comic_id": comic_id, "favorited": favorited}, headers={"Cache-Control": "no-store"})

    @app.post("/api/jm/favorites/{comic_id}")
    async def add_jm_favorite(comic_id: str):
        if not re.fullmatch(r"[0-9]{1,20}", comic_id) or int(comic_id) < 1:
            raise ComicApiError("漫画编号无效", 422, "invalid_request", "jm")
        plugin = jm_sync.require_account()
        if "add_favorite" not in plugin.capabilities:
            raise ComicApiError("当前禁漫插件不支持加入收藏", 400, "unsupported", "jm")
        return await service.call("jm", "add_favorite", comic_id)

    @app.delete("/api/jm/favorites/{comic_id}")
    async def remove_jm_favorite(comic_id: str):
        if not re.fullmatch(r"[0-9]{1,20}", comic_id) or int(comic_id) < 1:
            raise ComicApiError("漫画编号无效", 422, "invalid_request", "jm")
        plugin = jm_sync.require_account()
        if "remove_favorite" not in plugin.capabilities:
            raise ComicApiError("当前禁漫插件不支持取消收藏", 400, "unsupported", "jm")
        return await service.call("jm", "remove_favorite", comic_id)

    @app.get("/api/bika/favorites")
    async def bika_favorites(page: int = Query(1, ge=1, le=1000),
                             sort: str = Query("dd", pattern=r"^(dd|da|ld|vd)$")):
        if "favorites" not in service.source("bika").capabilities:
            raise ComicApiError("当前哔咔插件不支持收藏列表", 400, "unsupported", "bika")
        data = await service.call("bika", "favorites", page=page, sort=sort)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.get("/api/bika/favorites/{comic_id}")
    async def bika_favorite_status(comic_id: str):
        if "add_favorite" not in service.source("bika").capabilities:
            raise ComicApiError("当前哔咔插件不支持收藏状态", 400, "unsupported", "bika")
        data = await service.call("bika", "favorite_status", comic_id)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.post("/api/bika/favorites/{comic_id}")
    async def add_bika_favorite(comic_id: str):
        if "add_favorite" not in service.source("bika").capabilities:
            raise ComicApiError("当前哔咔插件不支持加入收藏", 400, "unsupported", "bika")
        return await service.call("bika", "add_favorite", comic_id)

    @app.delete("/api/bika/favorites/{comic_id}")
    async def remove_bika_favorite(comic_id: str):
        if "remove_favorite" not in service.source("bika").capabilities:
            raise ComicApiError("当前哔咔插件不支持取消收藏", 400, "unsupported", "bika")
        return await service.call("bika", "remove_favorite", comic_id)

    @app.get("/api/cron/preview")
    async def cron_preview(expression: str = Query(..., max_length=200)):
        try:
            return cron.preview(expression)
        except cron.CronError as exc:
            raise ComicApiError(str(exc), 422, "invalid_cron") from None

    def sync_routes(sync):
        prefix = f"/api/{sync.source}/sync"

        @app.get(f"{prefix}/settings", name=f"{sync.source}_sync_settings")
        async def sync_settings():
            return sync.options()

        @app.put(f"{prefix}/settings", name=f"save_{sync.source}_sync_settings")
        async def save_sync_settings(values: sync.settings_model):
            return sync.save_settings(values)

        @app.get(f"{prefix}/directories", name=f"{sync.source}_sync_directories")
        async def sync_directories(path: str = Query("", max_length=2048)):
            return await asyncio.to_thread(sync.paths.browse, path)

        @app.get(prefix, name=f"{sync.source}_sync_status")
        async def sync_status():
            return sync.status()

        @app.post(f"{prefix}/run", status_code=202, name=f"run_{sync.source}_sync")
        async def run_sync(values: SyncInput):
            album_ids = [sync.normalize_id(value) for value in values.album_ids] if values.album_ids else None
            return sync.launch(album_ids)

        @app.post(f"{prefix}/check-local", status_code=202, name=f"check_{sync.source}_local")
        async def check_local():
            return sync.launch(check_local=True)

        @app.post(f"{prefix}/repair", status_code=202, name=f"repair_{sync.source}_local")
        async def repair_local(values: SyncInput):
            album_ids = [sync.normalize_id(value) for value in values.album_ids] if values.album_ids else None
            return sync.launch(album_ids, repair=True)

        @app.get(f"{prefix}/local-status", name=f"{sync.source}_local_status")
        async def local_status(ids: str = Query("", max_length=8000)):
            album_ids = list(dict.fromkeys(sync.normalize_id(value) for value in ids.split(",") if value))
            if len(album_ids) > 200:
                raise ComicApiError("单次最多查询 200 本漫画", 422, "invalid_request", sync.source)
            data = await asyncio.to_thread(sync.local_status, album_ids)
            return JSONResponse(data, headers={"Cache-Control": "no-store"})

        @app.get(f"{prefix}/favorite-ids", name=f"{sync.source}_favorited_ids")
        async def favorited_ids(ids: str = Query("", max_length=8000)):
            # Reads only the locally recorded favorites, so search results can be marked without upstream calls.
            album_ids = list(dict.fromkeys(value for value in ids.split(",") if value))
            if len(album_ids) > 200:
                raise ComicApiError("单次最多查询 200 本漫画", 422, "invalid_request", sync.source)
            data = await asyncio.to_thread(sync.store.favorited_ids, sync.source, sync.owner(), album_ids)
            return JSONResponse({"ids": data}, headers={"Cache-Control": "no-store"})

        @app.get(f"{prefix}/catalog", name=f"{sync.source}_favorite_catalog")
        async def favorite_catalog(local: str = Query("", pattern=r"^(|downloaded|missing)$"),
                                   page: int = Query(1, ge=1, le=10000)):
            data = await asyncio.to_thread(sync.catalog, local, page)
            return JSONResponse(data, headers={"Cache-Control": "no-store"})

        @app.post(f"{prefix}/catalog/refresh", status_code=202, name=f"refresh_{sync.source}_favorite_catalog")
        async def refresh_favorite_catalog(sort: str = Query("", max_length=8)):
            return sync.refresh_catalog(sort or None)

        @app.post(f"{prefix}/cancel", name=f"cancel_{sync.source}_sync")
        async def cancel_sync():
            return sync.cancel()

    for sync in image_syncs.values():
        sync_routes(sync)

    @app.get("/api/library/sync/settings")
    async def library_image_settings():
        return library_images.options()

    @app.put("/api/library/sync/settings")
    async def save_library_image_settings(values: LibraryImageSettings):
        return library_images.save_settings(values)

    @app.get("/api/library/sync/directories")
    async def library_image_directories(path: str = Query("", max_length=2048)):
        return await asyncio.to_thread(library_images.paths.browse, path)

    @app.get("/api/library/sync")
    async def library_image_status():
        return JSONResponse(library_images.status(), headers={"Cache-Control": "no-store"})

    @app.post("/api/library/sync/run", status_code=202)
    async def run_library_images():
        return library_images.launch()

    @app.post("/api/library/sync/cancel")
    async def cancel_library_images():
        return library_images.cancel()

    @app.get("/api/sources/{source}/search-categories")
    async def search_categories(source: str):
        if "search_categories" not in service.source(source).capabilities:
            raise ComicApiError("图源不支持搜索分类筛选", 422, "unsupported", source)
        return {"categories": await service.call(source, "search_categories")}

    @app.get("/api/search")
    async def search(keyword: str = Query(min_length=1, max_length=200), source: str = "",
                     page: int = Query(1, ge=1, le=1000), sort: str = Query(default="", max_length=64),
                     mode: Literal["keyword", "tag", "author"] = "keyword",
                     tag_value: str = Query(default="", max_length=200),
                     category: str = Query(default="", max_length=200), deduplicate: bool = False):
        keyword, tag_value, category = keyword.strip(), tag_value.strip(), category.strip()
        if not keyword or (tag_value and (mode != "tag" or not source)):
            raise ComicApiError("搜索参数无效", 422, "invalid_request", source or None)
        if not source and (category or sort):
            raise ComicApiError("分类和排序仅适用于单一图源搜索", 422, "invalid_request")
        if source:
            items = await service.search_source(source, keyword, page, sort, mode, tag_value, category)
            return search_result_payload(items, {source: items}, {}, deduplicate=deduplicate)
        return await service.aggregate_search(keyword, page, mode, deduplicate=deduplicate)

    @app.get("/api/comic/{source}/{comic_id}")
    async def detail(source: str, comic_id: str):
        return await service.get_comic_detail(source, comic_id)

    @app.get("/api/chapter/{source}/{comic_id}/{chapter_id}")
    async def chapter(source: str, comic_id: str, chapter_id: str):
        urls = await service.get_chapter_images(source, comic_id, chapter_id)
        return {"images": ["/api/image/proxy?" + urlencode({"source": source, "url": url, "chapter_id": chapter_id}) for url in urls]}

    @app.get("/api/image/proxy")
    async def image(source: str, url: str, chapter_id: str = ""):
        if urlparse(url).scheme not in ("http", "https"):
            raise ComicApiError("图片地址无效", 400, "invalid_request")
        data = await service.image(source, url, chapter_id)
        try:
            with Image.open(io.BytesIO(data)) as img:
                media_type = Image.MIME.get(img.format, "image/jpeg")
        except Exception as exc:
            raise ComicApiError("图源未返回有效图片", 502, "invalid_image", source) from exc
        # One (source, url, chapter) always maps to the same bytes, so the browser may reuse it instead of
        # re-requesting covers on every re-render. Site auth still overrides this with no-store.
        return Response(data, media_type=media_type, headers={"Cache-Control": "private, max-age=604800"})

    no_store = {"Cache-Control": "no-store"}

    @app.get("/api/reading")
    async def reading_list():
        items = await asyncio.to_thread(store.reading_comics)
        return JSONResponse({"items": items}, headers=no_store)

    @app.delete("/api/reading/history")
    async def clear_reading_history():
        cleared = await asyncio.to_thread(store.clear_reading_history)
        return {"success": True, "cleared": cleared}

    @app.post("/api/reading/import")
    async def import_reading(data: ReadingImport):
        await asyncio.to_thread(store.import_reading, [item.model_dump() for item in data.comics],
                                [item.model_dump() for item in data.chapters])
        return {"success": True, "comics": len(data.comics), "chapters": len(data.chapters)}

    @app.get("/api/reading/{source}/{comic_id}")
    async def reading_detail(source: str, comic_id: str):
        data = await asyncio.to_thread(store.reading_comic, source, comic_id)
        return JSONResponse(data, headers=no_store)

    @app.put("/api/reading/{source}/{comic_id}")
    async def save_reading(source: str, comic_id: str, data: ReadingInput):
        if len(source) > 64 or len(comic_id) > 200:
            raise ComicApiError("请求参数无效", 422, "invalid_request")
        updated_at = await asyncio.to_thread(store.save_reading, source, comic_id, data.model_dump())
        return {"success": True, "updated_at": updated_at}

    @app.get("/api/library")
    async def library(category: str | None = None):
        all_books = store.books()
        return {"items": all_books if category is None else [item for item in all_books
                if category in item["library_categories"] or (category == "" and not item["library_categories"])],
                "categories": store.library_categories()}

    @app.put("/api/library/{source}/{comic_id}")
    async def save_book(source: str, comic_id: str, data: BookInput):
        metadata = await service.get_comic_detail(source, comic_id)
        store.save_book(source, comic_id, metadata, data.category)
        return {"success": True}

    @app.patch("/api/library/{source}/{comic_id}")
    async def categorize(source: str, comic_id: str, data: BookInput):
        if not store.set_category(source, comic_id, data.category):
            raise ComicApiError("未收藏该漫画", 404, "not_found")
        return {"success": True}

    @app.post("/api/library/{source}/{comic_id}/refresh")
    async def refresh(source: str, comic_id: str):
        books = [book for book in store.books() if book["source"] == source and book["id"] == comic_id]
        if not books:
            raise ComicApiError("未收藏该漫画", 404, "not_found")
        metadata = await service.get_comic_detail(source, comic_id)
        old_ids = {str(item["id"]) for item in books[0].get("chapters", [])}
        new_ids = {str(item["id"]) for item in metadata.get("chapters", [])}
        store.save_book(source, comic_id, metadata, books[0]["library_category"])
        return {"success": True, "new_chapters": len(new_ids - old_ids)}

    @app.delete("/api/library/{source}/{comic_id}")
    async def remove_book(source: str, comic_id: str):
        store.remove_book(source, comic_id)
        return {"success": True}

    @app.get("/api/downloads")
    async def task_list():
        return {"tasks": store.tasks()}

    @app.post("/api/downloads", status_code=202)
    async def create_download(data: TaskInput):
        return downloads.create(**data.model_dump())

    @app.get("/api/downloads/{task_id}")
    async def download_status(task_id: str):
        return downloads.require(task_id)

    @app.post("/api/downloads/{task_id}/cancel")
    async def cancel_download(task_id: str):
        return downloads.cancel(task_id)

    @app.post("/api/downloads/{task_id}/retry")
    async def retry_download(task_id: str):
        return downloads.retry(task_id)

    @app.delete("/api/downloads/{task_id}")
    async def delete_download(task_id: str):
        downloads.delete(task_id)
        return {"success": True}

    @app.get("/api/downloads/{task_id}/file")
    async def download_file(task_id: str):
        task = downloads.require(task_id)
        path = downloads.file_path(task_id)
        if task["status"] != "completed" or not path.is_file():
            raise ComicApiError("文件尚未生成或已被清理", 404, "file_not_ready")
        return FileResponse(path, filename=f"{task['password']}.pdf", media_type="application/pdf")

    @app.get("/api/download/{source}/{comic_id}/{chapter_id}")
    async def legacy_download(source: str, comic_id: str, chapter_id: str, title: str = "", chapter: str = ""):
        task = downloads.create(source, comic_id, chapter_id, title, chapter)
        deadline = asyncio.get_running_loop().time() + 600
        while True:
            current = downloads.require(task["id"])
            if current["status"] == "completed":
                return await download_file(task["id"])
            if current["status"] in ("failed", "cancelled"):
                raise ComicApiError(current["error"] or "任务已取消", 502, "download_failed", source)
            if asyncio.get_running_loop().time() > deadline:
                try:
                    downloads.cancel(task["id"])
                except ComicApiError:
                    pass
                raise ComicApiError("下载超时", 504, "download_timeout", source)
            await asyncio.sleep(0.5)

    @app.get("/api/{source}/{action}")
    async def browse(source: str, action: str, page: int = Query(1, ge=1, le=1000), sort: str = "", mode: str = "day", name: str = ""):
        plugin = service.source(source)
        if action not in {"latest", "category", "leaderboard", "random"} or action not in plugin.capabilities:
            raise ComicApiError("图源不支持此功能", 400, "unsupported", source)
        items = await service.call(source, "browse", action, page=page, sort=sort, mode=mode, name=name)
        return {"success": True, "source": source, "data": [dict(item, source=source) for item in items]}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8699)
