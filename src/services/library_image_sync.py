"""Scheduled local image sync, local check and repair for the books in the library.

Each source gets an ImageSync worker writing under the library's download root
(``<root>/<source>/...``) with its own checkpoint tables. A full run covers the library
categories selected in the settings; favorites pages and library cards can also sync
chosen books, adding favorites that are not in the library yet.
"""
import asyncio
import logging
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.errors import ComicApiError, DownloadCancelled
from src.services import cron
from src.services.favorites_library_sync import CATEGORIES
from src.services.image_sync import DEFAULT_ROOT, ROOTS_ENV, ImageSync, ImageSyncSettings
from src.services.local_images import DIRECTORY_RULES, FORMATS, DownloadPaths

log = logging.getLogger("comic.library_image_sync")
HISTORY_LIMIT = 10
# Counters a worker run reports and the library run sums across sources.
COUNTERS = ("albums_done", "downloaded", "skipped", "skipped_albums", "failed", "received_bytes",
            "local_albums", "local_images", "local_missing", "repaired_albums", "moved_images", "renamed_chapters")
LISTS = ("errors", "local_results", "renames")
MODES = ("sync", "check_local", "repair")


class LibraryImageSync:
    source = "library"
    label = "书库"
    settings_key = "library_image_sync"
    clock_key = "library_image_sync_clock"
    history_key = "library_image_sync_history"

    def __init__(self, store, service, favorites):
        self.store, self.service = store, service
        self.favorites = favorites
        self.workers = {}
        self.paths = DownloadPaths(store, ROOTS_ENV, DEFAULT_ROOT)
        self.next_run_at = store.settings(self.clock_key).get("next_run_at", 0)
        self.task = self.scheduler = None
        self.signal = threading.Event()
        self.wake = asyncio.Event()
        self.stopping = False
        self.child = None
        self.current = None
        # Set by the legacy migration, which must not move files while a run writes them.
        self.blocked_by = None

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def source_active(self, source):
        worker = self.workers.get(source)
        return bool(worker and worker.active)

    def settings(self):
        saved = self.store.settings(self.settings_key)
        config = ImageSyncSettings(**{key: saved[key] for key in ImageSyncSettings.model_fields
                                      if key in saved}).model_dump()
        config["download_dir"] = config["download_dir"] or str(self.paths.roots[0])
        return config

    def options(self):
        config = self.settings()
        books = self.store.books()
        counts = defaultdict(int)
        for book in books:
            for name in book["library_categories"] or [""]:
                counts[name] += 1
        categories = [{"value": name, "label": name, "books": counts[name]}
                      for name in dict.fromkeys([*self.store.library_categories(), *config["categories"]]) if name]
        categories.append({"value": "", "label": "未分类", "books": counts[""]})
        return {"settings": config, "schedule": cron.describe(config["cron"]), "timezone": cron.timezone_label(),
                "roots": [str(root) for root in self.paths.roots], "roots_env": ROOTS_ENV,
                "directory_rules": [{"value": key, "label": value} for key, value in DIRECTORY_RULES.items()],
                "formats": [{"value": key, "label": value} for key, value in FORMATS.items()],
                "categories": categories, "books": len(books),
                "scope_books": sum(self.in_scope(book, config["categories"]) for book in books)}

    @staticmethod
    def in_scope(book, categories):
        if not categories:
            return True
        names = book["library_categories"]
        return bool(set(names) & set(categories)) or (not names and "" in categories)

    def _history(self):
        return self.store.settings(self.history_key, [])[:HISTORY_LIMIT]

    def _persist(self, state):
        history = [item for item in self._history() if item.get("id") != state["id"]]
        self.store.save_settings(self.history_key, [state, *history][:HISTORY_LIMIT])

    def _schedule_next(self):
        self.next_run_at = cron.next_after(self.settings()["cron"], datetime.now()).timestamp()
        self.store.save_settings(self.clock_key, {"next_run_at": self.next_run_at})

    @staticmethod
    def _new_state(automatic=False, mode="sync", selected=False):
        return {"id": uuid.uuid4().hex, "automatic": automatic, "mode": mode, "selected": selected,
                "status": "running", "stage": "scanning", "started_at": time.time(), "finished_at": None,
                "albums_total": 0, "deferred_albums": 0, "added_books": 0, "backup_dir": "",
                **{key: 0 for key in COUNTERS}, **{key: [] for key in LISTS},
                "error": "", "title": "", "chapter": "", "page": 0, "pages": 0}

    def _workers(self):
        """Workers of every registered source that can sync images."""
        return [self.worker(source) for source, plugin in self.service.sources.items()
                if {"detail", "pages"} <= set(plugin.capabilities)]

    def local_counts(self):
        totals = dict.fromkeys(("downloaded", "missing", "error", "repairable"), 0)
        for worker in self._workers():
            try:
                for key, value in worker.local_counts().items():
                    totals[key] += value
            except ComicApiError:
                pass
        return totals

    def local_status(self, source, album_ids):
        worker = self.worker(source)
        return worker.local_status([worker.normalize_id(value) for value in album_ids])

    def status(self):
        current = dict(self.current) if self.current else None
        child = self.child.current if self.active and self.child and self.child.active else None
        if current and child:
            # The library run adds a worker's counters only when its source group ends; show live progress meanwhile.
            for key in COUNTERS:
                current[key] += child.get(key, 0)
            current.update({key: child.get(key) for key in ("stage", "title", "chapter", "page", "pages")})
        return {"active": self.active, "current": current, "history": self._history(), "paused": False,
                "local_counts": self.local_counts(),
                "next_run_at": self.next_run_at if self.settings()["enabled"] and not self.active else None}

    def save_settings(self, values):
        if self.active:
            raise ComicApiError("书库同步进行中，停止后才能修改设置", 409, "sync_active", self.source)
        config = values.model_dump()
        config["download_dir"] = str(self.paths.resolve(config["download_dir"] or self.paths.roots[0]))
        self.store.save_settings(self.settings_key, config)
        self.next_run_at = 0
        self.store.save_settings(self.clock_key, {"next_run_at": 0})
        self.wake.set()
        return self.options()

    async def start(self):
        for state in self._history():
            if state.get("status") in ("running", "cancelling"):
                state.update(status="interrupted", finished_at=time.time(), error="服务重启中断，下次同步继续")
                self._persist(state)
        self.stopping = False
        self.scheduler = asyncio.create_task(self._schedule())

    async def stop(self):
        self.stopping = True
        self.signal.set()
        self.wake.set()
        # The run awaits a worker task; stop that worker too, or shutdown waits for the whole source group.
        if self.child and self.child.active:
            self.child.cancel()
        if self.scheduler:
            await self.scheduler
        if self.task:
            await self.task

    async def _schedule(self):
        while not self.stopping:
            self.wake.clear()
            config = self.settings()
            if config["enabled"] and not self.active and time.time() >= self.next_run_at:
                try:
                    self.launch(automatic=True)
                except ComicApiError as exc:
                    state = self._new_state(True)
                    state.update(status="failed", error=str(exc), finished_at=time.time())
                    self._persist(state)
                    self._schedule_next()
            delay = max(1, min(60, self.next_run_at - time.time())) if config["enabled"] and not self.active else 60
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    def launch(self, automatic=False, mode="sync", items=None):
        """Start a run; ``items`` limits it to (source, comic ID) pairs and adds missing ones to the library."""
        if mode not in MODES:
            raise ValueError(f"Unknown library sync mode: {mode}")
        if self.active:
            raise ComicApiError("已有书库同步任务正在运行", 409, "sync_active", self.source)
        if self.blocked_by:
            raise ComicApiError(self.blocked_by, 409, "migration_active", self.source)
        if automatic and not self.settings()["enabled"]:
            raise ComicApiError("书库同步尚未开启", 409, "sync_disabled", self.source)
        if items is not None:
            items = [(source, self.worker(source).normalize_id(comic_id)) for source, comic_id in dict.fromkeys(items)]
            if mode == "repair" and any(comic_id not in self.worker(source).local_errors(self.worker(source).root())
                                        for source, comic_id in items):
                raise ComicApiError("只能修复本地检查标记为异常的漫画，需先重新检查本地漫画", 409, "not_repairable", self.source)
        elif mode == "repair" and not self.local_counts()["repairable"]:
            raise ComicApiError("当前保存目录没有待修复的异常漫画，需先检查本地漫画", 409, "nothing_to_repair", self.source)
        self.signal = threading.Event()
        self.current = self._new_state(automatic, mode, items is not None)
        if mode == "repair":
            self.current["backup_dir"] = str(Path(self.settings()["download_dir"]) / ".comic-api-repair" / self.current["id"])
        self._persist(self.current)
        self.task = asyncio.create_task(self._run(self.current, items))
        return dict(self.current)

    def cancel(self):
        if not self.active:
            raise ComicApiError("没有正在运行的书库同步任务", 409, "invalid_state", self.source)
        self.signal.set()
        self.current["status"] = "cancelling"
        self._persist(self.current)
        if self.child and self.child.active:
            self.child.cancel()
        return dict(self.current)

    def source_changed(self, source):
        # Only the disabled source's worker stops; the run then counts that group as deferred.
        if not self.service.is_enabled(source) and self.child and self.child.source == source and self.child.active:
            self.child.cancel()
        self.wake.set()

    def worker(self, source):
        if source not in self.workers:
            self.workers[source] = ImageSync(self, source)
        return self.workers[source]

    def _check(self):
        if self.signal.is_set():
            raise DownloadCancelled()

    @staticmethod
    def _note(state, title, message):
        if len(state["errors"]) < 100:
            state["errors"].append({"title": title, "message": message})

    def _add_book(self, source, comic_id, detail):
        if source in CATEGORIES:
            self.store.import_favorite_book(source, comic_id, detail, CATEGORIES[source])
        else:
            self.store.save_book(source, comic_id, detail)

    async def _selected_books(self, state, items):
        """Library books for the requested items; favorites not in the library yet are added first."""
        known = {(book["source"], str(book["id"])): book for book in await asyncio.to_thread(self.store.books)}
        books = []
        for source, comic_id in items:
            self._check()
            book = known.get((source, comic_id))
            if book is None and state["mode"] != "sync":
                state["failed"] += 1
                state["albums_done"] += 1
                self._note(state, comic_id, "漫画不在书库中")
                continue
            if book is None:
                state["stage"] = "adding"
                try:
                    detail = await self.service.get_comic_detail(source, comic_id)
                    await asyncio.to_thread(self._add_book, source, comic_id, detail)
                except DownloadCancelled:
                    raise
                except Exception as exc:
                    state["failed"] += 1
                    state["albums_done"] += 1
                    self._note(state, comic_id, f"加入书库失败：{exc}" if isinstance(exc, ComicApiError) else "加入书库失败")
                    continue
                state["added_books"] += 1
                book = dict(detail, source=source, id=comic_id)
            books.append(book)
        return books

    def _repairable(self, source, books):
        worker = self.worker(source)
        broken = set(worker.local_errors(worker.root()))
        result = []
        for book in books:
            try:
                if worker.normalize_id(str(book["id"])) in broken:
                    result.append(book)
            except ComicApiError:
                continue
        return result

    async def _sync_source(self, state, source, books):
        plugin = self.service.sources.get(source)
        if not plugin or not {"detail", "pages"} <= set(plugin.capabilities):
            state["failed"] += len(books)
            state["albums_done"] += len(books)
            self._note(state, source, "图源不存在或不支持图片同步")
            return
        favorite = self.favorites.get(source)
        if not self.service.is_enabled(source) or (favorite and favorite.account_changing):
            # The next library run picks these up once the source is enabled and its account settled.
            state["deferred_albums"] += len(books)
            state["albums_done"] += len(books)
            return
        worker = self.worker(source)
        pending = {}
        for book in books:
            try:
                pending[worker.normalize_id(str(book["id"]))] = book
            except ComicApiError as exc:
                state["failed"] += 1
                state["albums_done"] += 1
                self._note(state, book.get("title") or str(book["id"]), str(exc))
        if not pending:
            return
        if state["mode"] == "check_local" and not await asyncio.to_thread(worker.root().is_dir):
            # Nothing of this source exists locally yet; every book is simply not downloaded.
            for album_id, book in pending.items():
                await asyncio.to_thread(worker.save_local_status, worker.root(), album_id, "missing", book.get("title", ""))
            state["local_missing"] += len(pending)
            state["albums_done"] += len(pending)
            return
        backup = str(Path(state["backup_dir"]) / source) if state["backup_dir"] else ""
        worker.launch(list(pending), pending, state["mode"], backup)
        self.child = worker
        try:
            await worker.task
        finally:
            self.child = None
        child = worker.current
        for key in COUNTERS:
            state[key] += child.get(key, 0)
        for key in LISTS:
            state[key].extend(child.get(key, [])[:max(0, 100 - len(state[key]))])
        remaining = len(pending) - child.get("albums_done", 0)
        state["albums_done"] += remaining
        if child["status"] == "failed":
            state["failed"] += max(1, remaining)
            self._note(state, worker.label, child.get("error") or "图源同步失败")
        elif child["status"] == "cancelled":
            state["deferred_albums"] += remaining

    async def _run(self, state, items):
        try:
            groups = defaultdict(list)
            config = self.settings()
            if state["mode"] == "sync":
                await asyncio.to_thread(self.paths.prepare, config["download_dir"])
            if items is not None:
                books = await self._selected_books(state, items)
            else:
                books = [book for book in await asyncio.to_thread(self.store.books) if self.in_scope(book, config["categories"])]
            for book in books:
                groups[book["source"]].append(book)
            if state["mode"] == "repair":
                for source in list(groups):
                    if source in self.service.sources:
                        groups[source] = await asyncio.to_thread(self._repairable, source, groups[source])
            state["albums_total"] = state["albums_done"] + sum(len(items) for items in groups.values())
            state["stage"] = "downloading"
            self._persist(state)
            for source, books in groups.items():
                self._check()
                if not books:
                    continue
                try:
                    await self._sync_source(state, source, books)
                except DownloadCancelled:
                    raise
                except Exception as exc:
                    state["failed"] += len(books)
                    state["albums_done"] += len(books)
                    self._note(state, source, str(exc) if isinstance(exc, ComicApiError) else "图源同步失败")
                    log.warning("Library image sync failed for %s", source, exc_info=True)
                self._persist(state)
            self._check()
            state["status"] = "partial" if state["failed"] or state["deferred_albums"] else "completed"
        except DownloadCancelled:
            state["status"] = "interrupted" if self.stopping else "cancelled"
        except Exception as exc:
            state.update(status="failed", error=str(exc) if isinstance(exc, ComicApiError) else "书库同步失败")
            log.warning("Library image sync failed", exc_info=True)
        finally:
            state.update(finished_at=time.time(), stage="finished")
            self._persist(state)
            if state["mode"] == "sync" and not state["selected"]:
                self._schedule_next()
            self.wake.set()
