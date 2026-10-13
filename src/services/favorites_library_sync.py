"""Add cloud favorites to library collections without downloading images or deleting books."""
import asyncio
import logging
import time
from contextlib import asynccontextmanager

from pydantic import BaseModel, ConfigDict, Field

from src.errors import ComicApiError, DownloadCancelled

log = logging.getLogger("comic.favorites_library_sync")
CATEGORIES = {"jm": "JM 收藏", "bika": "哔咔收藏"}


class FavoritesLibrarySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(default=False, strict=True)
    interval_minutes: int = Field(default=60, ge=5, le=10080, strict=True)


class FavoritesLibrarySync:
    def __init__(self, store, service):
        self.store, self.service = store, service
        self.tasks = {}
        self.scheduler = None
        self.wake = asyncio.Event()
        self.stopping = False
        self.changing = set()
        self.generations = {source: 0 for source in CATEGORIES}
        self.states = {source: {"status": "idle", "next_run_at": 0,
                               **store.settings(f"library_sync_state:{source}")} for source in CATEGORIES}

    def settings(self, source):
        saved = self.store.settings(f"library_sync:{source}")
        return FavoritesLibrarySettings(**{key: saved[key] for key in FavoritesLibrarySettings.model_fields
                                          if key in saved}).model_dump()

    def identity(self, source):
        return self.store.account_owner((self.store.account(source) or {}).get("account"))

    def require_source(self, source):
        plugin = self.service.source(source, allow_disabled=True)
        if source not in CATEGORIES or "favorites" not in plugin.capabilities:
            raise ComicApiError("该图源不支持收藏同步到书库", 400, "unsupported", source)
        return plugin

    def require_account(self, source):
        self.require_source(source)
        plugin = self.service.source(source)
        if source in self.changing:
            raise ComicApiError("图源账号正在变更", 409, "account_changing", source)
        account = plugin.account_status()
        if not self.identity(source) or not (account.get("authenticated") or account.get("configured")):
            raise ComicApiError("收藏同步到书库需要绑定账号", 401, "login_required", source)

    def active(self, source):
        task = self.tasks.get(source)
        return task is not None and not task.done()

    def status(self, source):
        self.require_source(source)
        config = self.settings(source)
        paused = not self.service.is_enabled(source)
        current = dict(self.states[source])
        next_run_at = current.pop("next_run_at", 0)
        return {"settings": config, "category": CATEGORIES[source], "active": self.active(source),
                "paused": paused, "current": current,
                "next_run_at": next_run_at if config["enabled"] and not paused and not self.active(source) else None}

    def _persist(self, source):
        self.store.save_settings(f"library_sync_state:{source}", self.states[source])

    def save_settings(self, source, config):
        self.require_source(source)
        values = config.model_dump()
        if values["enabled"]:
            self.require_account(source)
            self.store.create_library_category(CATEGORIES[source])
        values["owner"] = self.identity(source)
        self.store.save_settings(f"library_sync:{source}", values)
        self.generations[source] += 1
        self.states[source]["next_run_at"] = 0
        self._persist(source)
        self.wake.set()
        return self.status(source)

    @asynccontextmanager
    async def account_change(self, source):
        if source not in CATEGORIES:
            yield
            return
        if source in self.changing:
            raise ComicApiError("图源账号正在变更", 409, "account_changing", source)
        self.changing.add(source)
        self.generations[source] += 1
        try:
            yield
            values = self.settings(source)
            values["enabled"] = False
            self.store.save_settings(f"library_sync:{source}", values)
            self.states[source] = {"status": "idle", "next_run_at": 0}
            self._persist(source)
        finally:
            self.changing.discard(source)
            self.wake.set()

    def source_changed(self, source):
        if source not in CATEGORIES:
            return
        if not self.service.is_enabled(source):
            self.generations[source] += 1
        self.wake.set()

    async def start(self):
        self.stopping = False
        for source, state in self.states.items():
            if state["status"] == "running":
                state.update(status="interrupted", next_run_at=0, error="服务重启中断，下次同步继续增补书库")
                self._persist(source)
        self.scheduler = asyncio.create_task(self._schedule())

    async def stop(self):
        self.stopping = True
        self.wake.set()
        if self.scheduler:
            await self.scheduler
        await asyncio.gather(*self.tasks.values())

    async def _schedule(self):
        while not self.stopping:
            self.wake.clear()
            for source in CATEGORIES:
                if (not self.service.is_enabled(source) or source in self.changing or self.active(source)
                        or not self.settings(source)["enabled"] or time.time() < self.states[source]["next_run_at"]):
                    continue
                try:
                    self.launch(source)
                except ComicApiError as exc:
                    self.states[source].update(status="failed", error=str(exc))
                    self._schedule_next(source)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    def _schedule_next(self, source):
        self.states[source]["next_run_at"] = time.time() + self.settings(source)["interval_minutes"] * 60
        self._persist(source)

    def launch(self, source):
        self.require_account(source)
        if not self.settings(source)["enabled"]:
            raise ComicApiError("收藏同步到书库尚未开启", 409, "sync_disabled", source)
        if self.active(source):
            raise ComicApiError("收藏同步到书库正在运行", 409, "sync_active", source)
        owner = self.identity(source)
        if self.store.settings(f"library_sync:{source}").get("owner") != owner:
            self.save_settings(source, FavoritesLibrarySettings(**{**self.settings(source), "enabled": False}))
            raise ComicApiError("图源账号已变更，收藏同步到书库已关闭", 409, "account_changed", source)
        self.states[source] = {"status": "running", "started_at": time.time(), "finished_at": None,
                               "total": 0, "processed": 0, "added": 0, "failed": 0, "errors": [],
                               "error": "", "next_run_at": 0}
        self._persist(source)
        self.tasks[source] = asyncio.create_task(self._run(source, owner, self.generations[source]))
        return self.status(source)

    def _check(self, source, owner, generation):
        if (self.stopping or generation != self.generations[source] or source in self.changing
                or not self.service.is_enabled(source) or not self.settings(source)["enabled"]):
            raise DownloadCancelled()
        if owner != self.identity(source):
            self.save_settings(source, FavoritesLibrarySettings(**{**self.settings(source), "enabled": False}))
            raise ComicApiError("同步期间图源账号发生变更，自动同步已关闭", 409, "account_changed", source)

    async def _collect(self, source, owner, generation):
        result, signatures = {}, set()
        expected_total = None
        kwargs = {"folder_id": "0", "order": "mr"} if source == "jm" else {"sort": "dd"}
        for page in range(1, 1001):
            self._check(source, owner, generation)
            data = await self.service.call(source, "favorites", page=page, **kwargs)
            self._check(source, owner, generation)
            signature = tuple(str(item["id"]) for item in data["items"])
            if signature and signature in signatures:
                raise ComicApiError("收藏分页重复，本轮未导入书库", 502, "invalid_response", source)
            signatures.add(signature)
            total = data.get("total")
            if page == 1:
                expected_total = total
            elif total != expected_total:
                raise ComicApiError("收藏在扫描期间发生变化，需要重新同步", 409, "favorites_changed", source)
            for item in data["items"]:
                comic_id = (self.store._jm_comic_id(item["id"]) if source == "jm"
                            else self.store._bika_comic_id(item["id"]))
                result[comic_id] = item
            if not data["has_next"]:
                if expected_total is not None and len(result) != expected_total:
                    raise ComicApiError("收藏分页数量不一致，本轮未导入书库", 502, "invalid_response", source)
                return result
            if not signature:
                raise ComicApiError("收藏分页提前返回空页", 502, "invalid_response", source)
        raise ComicApiError("收藏超过 1000 页扫描上限", 422, "sync_too_large", source)

    async def _run(self, source, owner, generation):
        state = self.states[source]
        try:
            favorites = await self._collect(source, owner, generation)
            state["total"] = len(favorites)
            books = {book["id"]: book for book in self.store.books() if book["source"] == source}
            for comic_id in favorites:
                self._check(source, owner, generation)
                try:
                    metadata = books.get(comic_id) or await self.service.get_comic_detail(source, comic_id)
                    self._check(source, owner, generation)
                    added = self.store.import_favorite_book(source, comic_id, metadata, CATEGORIES[source])
                    state["added"] += int(added)
                except DownloadCancelled:
                    raise
                except ComicApiError as exc:
                    self._check(source, owner, generation)
                    if exc.status_code in (401, 403) or exc.code == "account_changed":
                        raise
                    state["failed"] += 1
                    if len(state["errors"]) < 20:
                        state["errors"].append({"comic_id": comic_id, "message": str(exc)})
                state["processed"] += 1
                self._persist(source)
                # Existing books need no network request; still allow settings/shutdown to interrupt.
                await asyncio.sleep(0)
            state["status"] = "partial" if state["failed"] else "completed"
        except DownloadCancelled:
            state["status"] = "stopped"
        except Exception as exc:
            state.update(status="failed", error=str(exc) if isinstance(exc, ComicApiError) else "收藏读取或书库写入失败")
            log.warning("Favorites library sync failed for %s", source, exc_info=True)
        finally:
            state["finished_at"] = time.time()
            if self.states[source] is state:
                if generation == self.generations[source]:
                    self._schedule_next(source)
                else:
                    self._persist(source)
            self.wake.set()
