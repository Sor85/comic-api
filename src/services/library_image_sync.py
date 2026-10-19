"""Scheduled local image sync for every book in the library.

Each source gets a FavoriteSync worker writing under the library's own download root
(``<root>/<source>/...``) with its own checkpoint tables, so all download, verify and
resume logic stays shared. JM/Bika books already completed by their favorites sync are
skipped instead of being downloaded a second time.
"""
import asyncio
import hashlib
import logging
import re
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.errors import ComicApiError, DownloadCancelled
from src.services import cron
from src.services.favorite_sync import BaseSyncSettings, FavoriteSync
from src.services.jm_naming import JM_DIRECTORY_RULE
from src.services.local_images import DIRECTORY_RULES, FORMATS, DownloadPaths

log = logging.getLogger("comic.library_image_sync")
HISTORY_LIMIT = 10
ROOTS_ENV = "LIBRARY_DOWNLOAD_ROOTS"
DEFAULT_ROOT = "library"
# Counters a worker run reports and the library run sums across sources.
COUNTERS = ("albums_done", "downloaded", "skipped", "skipped_albums", "external_albums", "failed", "received_bytes")


class LibraryImageSettings(BaseSyncSettings):
    """Same download options as favorites sync; the JM-only Pindextitle rule needs JM chapter metadata."""


def table_prefix(source):
    safe = re.sub(r"[^a-z0-9_]", "_", source)
    # Distinct plugin IDs that only differ by '-'/'_' must not share checkpoint tables.
    return f"library_{safe}" if safe == source else f"library_{safe}_{hashlib.sha256(source.encode()).hexdigest()[:8]}"


class LibrarySourceSync(FavoriteSync):
    """Library images of one source; ``favorite`` is that source's favorites sync, if any."""
    roots_env = ROOTS_ENV
    default_root = DEFAULT_ROOT
    id_pattern = r"[A-Za-z0-9_-]{1,200}"

    def __init__(self, library, source, favorite=None):
        self.library, self.source, self.favorite = library, source, favorite
        self.label = library.service.source(source, allow_disabled=True).name
        super().__init__(library.store, library.service)
        prefix = table_prefix(source)
        library.store.ensure_sync_tables(prefix)
        self.runs_table, self.files_table = f"{prefix}_sync_runs", f"{prefix}_sync_files"
        self.local_table, self.status_table = f"{prefix}_sync_local_files", f"{prefix}_local_status"
        self.settings_key, self.clock_key = f"{prefix}_sync", f"{prefix}_sync_clock"

    def chapter_id(self, value):
        return self.comic_id(value)

    def settings(self):
        config = self.library.settings()
        config["download_dir"] = str(Path(config["download_dir"]) / self.source)
        return config

    def synced_elsewhere(self, album_id, book):
        return self.favorite.synced_copy(album_id, book, self.signal) if self.favorite else None


class LibraryImageSync:
    source = "library"
    label = "书库"
    settings_key = "library_image_sync"
    clock_key = "library_image_sync_clock"
    history_key = "library_image_sync_history"
    directory_rules = {key: value for key, value in DIRECTORY_RULES.items() if key != JM_DIRECTORY_RULE}

    def __init__(self, store, service, image_syncs):
        self.store, self.service = store, service
        self.image_syncs = image_syncs
        self.workers = {}
        self.paths = DownloadPaths(store, self.source, ROOTS_ENV, DEFAULT_ROOT, str)
        self.next_run_at = store.settings(self.clock_key).get("next_run_at", 0)
        self.task = self.scheduler = None
        self.signal = threading.Event()
        self.wake = asyncio.Event()
        self.stopping = False
        self.child = None
        self.current = None

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def settings(self):
        saved = self.store.settings(self.settings_key)
        config = LibraryImageSettings(**{key: saved[key] for key in LibraryImageSettings.model_fields
                                         if key in saved}).model_dump()
        config["download_dir"] = config["download_dir"] or str(self.paths.roots[0])
        return config

    def options(self):
        config = self.settings()
        return {"settings": config, "schedule": cron.describe(config["cron"]), "timezone": cron.timezone_label(),
                "roots": [str(root) for root in self.paths.roots], "roots_env": ROOTS_ENV,
                "directory_rules": [{"value": key, "label": value} for key, value in self.directory_rules.items()],
                "formats": [{"value": key, "label": value} for key, value in FORMATS.items()],
                "books": len(self.store.books())}

    def _history(self):
        return self.store.settings(self.history_key, [])[:HISTORY_LIMIT]

    def _persist(self, state):
        history = [item for item in self._history() if item.get("id") != state["id"]]
        self.store.save_settings(self.history_key, [state, *history][:HISTORY_LIMIT])

    def _schedule_next(self):
        self.next_run_at = cron.next_after(self.settings()["cron"], datetime.now()).timestamp()
        self.store.save_settings(self.clock_key, {"next_run_at": self.next_run_at})

    @staticmethod
    def _new_state(automatic=False):
        return {"id": uuid.uuid4().hex, "automatic": automatic, "mode": "library", "status": "running",
                "stage": "scanning", "started_at": time.time(), "finished_at": None,
                "albums_total": 0, "albums_done": 0, "downloaded": 0, "skipped": 0,
                "skipped_albums": 0, "external_albums": 0, "deferred_albums": 0, "failed": 0,
                "received_bytes": 0, "errors": [], "error": "", "title": "", "chapter": "", "page": 0, "pages": 0}

    def status(self):
        current = dict(self.current) if self.current else None
        child = self.child.current if self.active and self.child and self.child.active else None
        if current and child:
            # The library run adds a worker's counters only when its source group ends; show live progress meanwhile.
            for key in COUNTERS:
                current[key] += child.get(key, 0)
            current.update({key: child.get(key) for key in ("stage", "title", "chapter", "page", "pages")})
        return {"active": self.active, "current": current, "history": self._history(), "paused": False,
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

    def launch(self, automatic=False):
        if self.active:
            raise ComicApiError("已有书库同步任务正在运行", 409, "sync_active", self.source)
        if automatic and not self.settings()["enabled"]:
            raise ComicApiError("书库同步尚未开启", 409, "sync_disabled", self.source)
        self.signal = threading.Event()
        self.current = self._new_state(automatic)
        self._persist(self.current)
        self.task = asyncio.create_task(self._run(self.current))
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
            self.workers[source] = LibrarySourceSync(self, source, self.image_syncs.get(source))
        return self.workers[source]

    def _check(self):
        if self.signal.is_set():
            raise DownloadCancelled()

    @staticmethod
    def _note(state, title, message):
        if len(state["errors"]) < 100:
            state["errors"].append({"title": title, "message": message})

    async def _sync_source(self, state, source, books):
        plugin = self.service.sources.get(source)
        if not plugin or not {"detail", "pages"} <= set(plugin.capabilities):
            state["failed"] += len(books)
            state["albums_done"] += len(books)
            self._note(state, source, "图源不存在或不支持图片同步")
            return
        favorite = self.image_syncs.get(source)
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
        worker.launch(album_ids=list(pending), library_books=pending)
        self.child = worker
        try:
            await worker.task
        finally:
            self.child = None
        child = worker.current
        for key in COUNTERS:
            state[key] += child.get(key, 0)
        state["errors"].extend(child.get("errors", [])[:max(0, 100 - len(state["errors"]))])
        remaining = len(pending) - child.get("albums_done", 0)
        state["albums_done"] += remaining
        if child["status"] == "failed":
            state["failed"] += max(1, remaining)
            self._note(state, worker.label, child.get("error") or "图源同步失败")
        elif child["status"] in ("cancelled", "interrupted"):
            state["deferred_albums"] += remaining

    async def _run(self, state):
        try:
            groups = defaultdict(list)
            await asyncio.to_thread(self.paths.prepare, self.settings()["download_dir"])
            for book in await asyncio.to_thread(self.store.books):
                groups[book["source"]].append(book)
            state["albums_total"] = sum(len(items) for items in groups.values())
            state["stage"] = "downloading"
            self._persist(state)
            for source, books in groups.items():
                self._check()
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
            self._schedule_next()
            self.wake.set()
