"""Per-source image download for library sync, with durable per-image checkpoints.

LibraryImageSync runs one ImageSync worker per source. A worker downloads, checks existing
local copies of, or repairs the library books of its source under ``<save dir>/<source>``.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.errors import ComicApiError, DownloadCancelled
from src.services import cron
from src.services.local_check import LocalLibrary, local_file_valid
from src.services.local_images import DownloadPaths, file_digest, receive_image, write_atomic

ROOTS_ENV = "LIBRARY_DOWNLOAD_ROOTS"
DEFAULT_ROOT = "library"
GENERIC_ID = r"[A-Za-z0-9_-]{1,200}"
# Built-in sources keep their stricter ID formats so directory names match exactly; plugins use the generic form.
COMIC_IDS = {"jm": r"[0-9]{1,20}", "bika": r"[0-9a-f]{24}"}
CHAPTER_IDS = {"jm": r"[0-9]{1,20}", "bika": r"[0-9]{1,20}"}
# Failures that describe the local copy; upstream and session errors leave the recorded local state unchanged.
LOCAL_FAILURES = {"local_incomplete", "local_ambiguous", "local_invalid_image", "local_conflict", "local_changed",
                  "local_unmatched", "directory_unavailable", "invalid_directory", "file_conflict"}


class ImageSyncSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    cron: str = Field(default=cron.DEFAULT, max_length=200)
    download_dir: str = Field(default="", max_length=2048)
    speed_limit_kbps: int = Field(default=0, ge=0, le=1048576)
    image_format: Literal["original", "jpeg", "png", "webp"] = "original"
    quality: int = Field(default=90, ge=1, le=100)
    directory_rule: Literal["id", "title", "title_plain", "author"] = "title"
    retries: int = Field(default=2, ge=0, le=5)
    # Library categories a full sync covers; empty covers the whole library, "" stands for uncategorized books.
    categories: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("cron")
    @classmethod
    def valid_cron(cls, value):
        return cron.validate(value)

    @field_validator("categories")
    @classmethod
    def valid_categories(cls, values):
        if any(len(value) > 100 for value in values):
            raise ValueError("分类名称过长")
        return list(dict.fromkeys(values))


def table_prefix(source):
    safe = re.sub(r"[^a-z0-9_]", "_", source)
    # Distinct plugin IDs that only differ by '-'/'_' must not share checkpoint tables.
    return f"library_{safe}" if safe == source else f"library_{safe}_{hashlib.sha256(source.encode()).hexdigest()[:8]}"


class ImageSync:
    def __init__(self, library, source):
        self.library, self.store, self.service, self.source = library, library.store, library.service, source
        self.label = self.service.source(source, allow_disabled=True).name
        self.id_pattern = COMIC_IDS.get(source, GENERIC_ID)
        self.chapter_pattern = CHAPTER_IDS.get(source, GENERIC_ID)
        prefix = table_prefix(source)
        self.store.ensure_sync_tables(prefix)
        self.files_table, self.local_table = f"{prefix}_sync_files", f"{prefix}_sync_local_files"
        self.status_table = f"{prefix}_local_status"
        self.log = logging.getLogger(f"comic.library_sync.{source}")
        self.paths = DownloadPaths(self.store, ROOTS_ENV, DEFAULT_ROOT, self.comic_id, self.chapter_id)
        self.task = None
        self.signal = threading.Event()
        self.current = None

    def comic_id(self, value):
        value = str(value).lower() if self.source == "bika" else str(value)
        if not re.fullmatch(self.id_pattern, value):
            raise ComicApiError(f"{self.label}返回了无效编号", 502, "invalid_response", self.source)
        return value

    def normalize_id(self, value):
        """Validate a client-supplied comic ID; raises 422 instead of the upstream 502."""
        if isinstance(value, str) and self.source == "bika":
            value = value.lower()
        if not isinstance(value, str) or not re.fullmatch(self.id_pattern, value):
            raise ComicApiError("漫画编号无效", 422, "invalid_request", self.source)
        return value

    def chapter_id(self, value):
        value = str(value)
        if not re.fullmatch(self.chapter_pattern, value):
            raise ComicApiError(f"{self.label}返回了无效章节编号", 502, "invalid_response", self.source)
        return value

    def settings(self):
        config = self.library.settings()
        config["download_dir"] = str(Path(config["download_dir"]) / self.source)
        return config

    def root(self):
        return self.paths.resolve(self.settings()["download_dir"])

    def identity(self):
        account = self.store.account(self.source) or {}
        return hashlib.sha256(str(account.get("account", "")).encode()).hexdigest()

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    @staticmethod
    def _new_state(mode):
        return {"id": uuid.uuid4().hex, "mode": mode, "status": "running", "stage": "scanning",
                "started_at": time.time(), "finished_at": None, "albums_total": 0, "albums_done": 0,
                "downloaded": 0, "skipped": 0, "skipped_albums": 0, "failed": 0, "received_bytes": 0,
                "local_albums": 0, "local_images": 0, "local_missing": 0, "local_results": [],
                "repaired_albums": 0, "moved_images": 0, "renamed_chapters": 0, "renames": [], "backup_dir": "",
                "title": "", "chapter": "", "page": 0, "pages": 0, "errors": [], "error": ""}

    def launch(self, album_ids, books, mode="sync", backup_dir=""):
        """Start ``mode`` (sync, check_local or repair) for ``album_ids``; ``books`` maps them to library metadata."""
        if self.active:
            raise ComicApiError("已有书库同步任务正在运行", 409, "sync_active", self.source)
        plugin = self.service.source(self.source)
        if not {"detail", "pages"} <= set(plugin.capabilities):
            raise ComicApiError("图源不支持图片同步", 400, "unsupported", self.source)
        config = self.settings()
        if mode == "repair" and set(album_ids) - set(self.local_errors(self.paths.resolve(config["download_dir"]))):
            raise ComicApiError("只能修复本地检查标记为异常的漫画，需先重新检查本地漫画", 409, "not_repairable", self.source)
        self.signal = threading.Event()
        self.current = self._new_state(mode)
        self.current.update(settings=config, backup_dir=backup_dir)
        self.task = asyncio.create_task(self._run(config, list(album_ids), self.identity(), books))
        return dict(self.current)

    def cancel(self):
        if self.active:
            self.signal.set()
            self.current["status"] = "cancelling"

    def _check(self, identity):
        if self.signal.is_set():
            raise DownloadCancelled()
        if identity != self.identity():
            raise ComicApiError(f"同步期间{self.label}账号发生变更", 409, "account_changed", self.source)

    def _error(self, exc, title="", album_id=None):
        self.current["failed"] += 1
        message = str(exc) if isinstance(exc, ComicApiError) else "本地写入或图源请求失败"
        if len(self.current["errors"]) < 100:
            entry = {"title": title, "message": message}
            if album_id:
                entry["album_id"] = album_id
            self.current["errors"].append(entry)
        self.log.warning("%s sync item failed: %s", self.source, message, exc_info=not isinstance(exc, ComicApiError))
        if isinstance(exc, ComicApiError) and (exc.status_code in (401, 403) or exc.code == "account_changed"):
            raise exc

    async def _run(self, config, album_ids, identity, books):
        state = self.current
        checking, repairing = state["mode"] == "check_local", state["mode"] == "repair"
        prepared = False
        try:
            state["albums_total"] = len(album_ids)
            local = None
            if checking:
                local = await asyncio.to_thread(LocalLibrary, self.store, self.paths, config, books.values(), self.signal, self)
            elif repairing:
                local = await asyncio.to_thread(LocalLibrary, self.store, self.paths, config, [], self.signal, self)
            state["stage"] = "checking" if checking else "repairing" if repairing else "downloading"
            for album_id in album_ids:
                self._check(identity)
                try:
                    album_id = self.comic_id(album_id)
                    if checking:
                        await self._check_local_album(album_id, config, identity, local)
                    elif repairing:
                        await self._repair_album(album_id, config, identity, local)
                    else:
                        # Reset first so a failed detail read is not reported under the previous title.
                        state.update(title=books[album_id].get("title") or album_id, chapter="", page=0, pages=0)
                        book = await self.service.get_comic_detail(self.source, album_id)
                        complete = await asyncio.to_thread(self.completed_album, album_id, config, book)
                        self._check(identity)
                        if complete is not None:
                            state.update(title=book.get("title", album_id), chapter="", page=0, pages=0)
                            state["skipped_albums"] += 1
                            state["skipped"] += complete
                        else:
                            if not prepared:
                                await asyncio.to_thread(self.paths.prepare, config["download_dir"])
                                prepared = True
                            await self._album(album_id, config, identity, book)
                except DownloadCancelled:
                    raise
                except Exception as exc:
                    label = state["title"] or album_id
                    if (checking or repairing) and state["chapter"]:
                        label += f" / {state['chapter']}"
                    self._error(exc, label, album_id)
                state["albums_done"] += 1
            self._check(identity)
            state["status"] = "partial" if state["failed"] else "completed"
        except DownloadCancelled:
            state["status"] = "cancelled"
        except Exception as exc:
            if self.signal.is_set():
                state["status"] = "cancelled"
            else:
                state.update(status="failed", error=str(exc) if isinstance(exc, ComicApiError) else "同步失败，需检查目录权限和图源状态")
                self.log.warning("%s sync failed", self.source, exc_info=True)
        finally:
            state.update(finished_at=time.time(), stage="finished")

    async def _chapter_data(self, album_id, chapter):
        chapter_id = self.chapter_id(chapter["id"])
        return await self.service.get_chapter_images(self.source, album_id, chapter_id), chapter

    async def _check_local_album(self, album_id, config, identity, local):
        state = self.current
        state.update(title=album_id, chapter="", page=0, pages=0)
        book = await self.service.get_comic_detail(self.source, album_id)
        state["title"] = book["title"]
        local.add_title(book)
        directory = None
        try:
            directory = await asyncio.to_thread(local.album, book)
            if not directory:
                state["local_missing"] += 1
                self.save_local_status(local.root, album_id, "missing", book["title"])
                return
            await self._verify_local_album(album_id, config, identity, local, book, directory)
        except DownloadCancelled:
            raise
        except Exception as exc:
            if self._local_failure(exc):
                self.save_local_status(local.root, album_id, "error", book["title"], directory or "", message=self._message(exc))
            raise

    async def _verify_local_album(self, album_id, config, identity, local, book, directory, prepared=None):
        """Validate every chapter of a matched directory, then register it as downloaded."""
        state = self.current
        chapters = book.get("chapters", [])
        if not chapters:
            raise ComicApiError("漫画暂无有效章节", 502, "invalid_response", self.source)
        records, used = [], set()
        for index, chapter in enumerate(chapters, 1):
            self._check(identity)
            state.update(chapter=chapter.get("name", ""), page=0, pages=0)
            chapter_id = self.chapter_id(chapter["id"])
            urls, chapter = prepared[index - 1] if prepared else await self._chapter_data(album_id, chapter)
            if not urls:
                raise ComicApiError("章节未返回图片", 502, "invalid_response", self.source)
            state["chapter"] = chapter.get("name", "")
            target = await asyncio.to_thread(local.chapter, directory, chapter, index)
            if target in used:
                raise ComicApiError("多个章节匹配到同一目录，未认定为已下载", 409, "local_ambiguous", self.source)
            used.add(target)
            state["pages"] = len(urls)
            records.extend(await asyncio.to_thread(local.validate_chapter, album_id, chapter_id, target, urls))
            state["page"] = len(urls)
        self._check(identity)
        await asyncio.to_thread(local.remember, album_id, records)
        self.save_local_status(local.root, album_id, "downloaded", book["title"], directory, len(records))
        await asyncio.to_thread(self.remember_complete, album_id, config, book, records)
        state["local_albums"] += 1
        state["local_images"] += len(records)
        if len(state["local_results"]) < 100:
            state["local_results"].append({"id": album_id, "title": book["title"], "path": str(directory), "images": len(records)})
        return records

    @staticmethod
    def _message(exc):
        return str(exc) if isinstance(exc, ComicApiError) else "本地读取或图源请求失败"

    @staticmethod
    def _local_failure(exc):
        return isinstance(exc, OSError) or isinstance(exc, ComicApiError) and exc.code in LOCAL_FAILURES

    def save_local_status(self, root, album_id, status, title="", path="", images=0, message=""):
        with self.store.connect() as db:
            db.execute(f"""INSERT INTO {self.status_table} VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(root, album_id) DO UPDATE SET status=excluded.status, title=excluded.title, path=excluded.path,
                images=excluded.images, message=excluded.message, checked_at=excluded.checked_at""",
                       (str(root), album_id, status, title or "", str(path or ""), images, message[:500], time.time()))
        if status != "downloaded":
            self._drop_complete(root, album_id)

    def _completion_key(self, root, album_id):
        # One row per album keeps writes O(1).
        return f"image_sync_complete:{self.source}:{hashlib.sha256(str(root).encode()).hexdigest()[:16]}:{album_id}"

    def _drop_complete(self, root, album_id):
        self.store.delete_settings(self._completion_key(root, album_id))

    @staticmethod
    def output_scope(config):
        return hashlib.sha256(json.dumps([config[key] for key in
                                         ("download_dir", "directory_rule", "image_format", "quality")]).encode()).hexdigest()

    @staticmethod
    def file_stamp(path):
        stat = path.stat()
        return [str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]

    @staticmethod
    def book_stamp(book):
        return json.dumps([book.get("title"), book.get("author"), book.get("page_count"),
                           [[str(chapter["id"]), chapter.get("name"), chapter.get("order"), chapter.get("page_count")]
                            for chapter in book.get("chapters", [])]], ensure_ascii=False)

    def remember_complete(self, album_id, config, book, files):
        # Checked external files arrive as records; downloads already captured their post-validation stat.
        files = [self.file_stamp(self.paths.resolve(item["path"])) if isinstance(item, dict) else item for item in files]
        root = self.paths.resolve(config["download_dir"])
        self.store.save_settings(self._completion_key(root, album_id), {
            "scope": self.output_scope(config), "metadata": self.book_stamp(book), "files": files,
            "checked_at": time.time()})

    def completed_album(self, album_id, config, book):
        """Fast pass: skip page requests and hashing when chapters, output settings and file stats are unchanged."""
        root = str(self.paths.resolve(config["download_dir"]))
        entry = self.store.settings(self._completion_key(root, album_id))
        if (not entry or entry.get("scope") != self.output_scope(config)
                or entry.get("metadata") != self.book_stamp(book)):
            return None
        try:
            files = entry.get("files", [])
            if not files:
                return None
            for stamp in files:
                if self.signal.is_set():
                    raise DownloadCancelled()
                path = self.paths.resolve(stamp[0])
                if not path.is_relative_to(Path(root)) or not path.is_file() or self.file_stamp(path) != stamp:
                    return None
            return len(files)
        except (OSError, ComicApiError):
            return None

    def local_errors(self, root):
        """Repairable albums: marked broken by a check or sync with a uniquely matched directory."""
        with self.store.connect() as db:
            return [row[0] for row in db.execute(
                f"SELECT album_id FROM {self.status_table} WHERE root=? AND status='error' AND path<>'' ORDER BY checked_at",
                (str(root),))]

    def local_counts(self):
        root = str(self.root())
        with self.store.connect() as db:
            counts = {row[0]: row[1] for row in db.execute(
                f"SELECT status, COUNT(*) FROM {self.status_table} WHERE root=? GROUP BY status", (root,))}
            counts["repairable"] = db.execute(
                f"SELECT COUNT(*) FROM {self.status_table} WHERE root=? AND status='error' AND path<>''", (root,)).fetchone()[0]
        return {key: counts.get(key, 0) for key in ("downloaded", "missing", "error", "repairable")}

    def local_status(self, album_ids):
        """Per-comic local state under the configured save directory."""
        root = str(self.root())
        items = {}
        with self.store.connect() as db:
            for album_id in album_ids:
                row = db.execute(f"SELECT * FROM {self.status_table} WHERE root=? AND album_id=?", (root, album_id)).fetchone()
                if row:
                    items[album_id] = {key: row[key] for key in ("status", "title", "path", "images", "message", "checked_at")}
        return {"root": root, "items": items}

    def _guard_plain_directory(self, album_id, directory):
        prefix = str(directory) + os.sep
        with self.store.connect() as db:
            for table in (self.files_table, self.local_table):
                conflict = db.execute(f"SELECT 1 FROM {table} WHERE album_id<>? AND substr(path,1,?)=? COLLATE NOCASE LIMIT 1",
                                      (album_id, len(prefix), prefix)).fetchone()
                if conflict:
                    raise ComicApiError("无 ID 漫画目录已关联其他漫画，未混合写入；需改用带 ID 规则", 409, "file_conflict", self.source)

    async def _album(self, album_id, config, identity, book):
        state = self.current
        state.update(title=book["title"], chapter="", page=0, pages=0)
        chapters = book.get("chapters", [])
        if not chapters:
            raise ComicApiError("漫画暂无有效章节", 502, "invalid_response", self.source)
        if config["directory_rule"] == "title_plain":
            self._guard_plain_directory(album_id, self.paths.album(config, book))
        failed, errors, images = state["failed"], len(state["errors"]), 0
        files = []
        for index, chapter in enumerate(chapters, 1):
            self._check(identity)
            state.update(chapter=chapter.get("name", ""), page=0, pages=0)
            try:
                chapter_id = self.chapter_id(chapter["id"])
                urls, chapter = await self._chapter_data(album_id, chapter)
                if not urls:
                    raise ComicApiError("章节未返回图片", 502, "invalid_response", self.source)
                state.update(chapter=chapter.get("name", ""), pages=len(urls))
                directory = self.paths.chapter(config, book, chapter, index)
                for page, url in enumerate(urls, 1):
                    self._check(identity)
                    state["page"] = page
                    try:
                        skipped, received, stamp = await asyncio.to_thread(self._image, config, album_id, chapter_id, page, url, directory)
                        files.append(stamp)
                        state["skipped" if skipped else "downloaded"] += 1
                        state["received_bytes"] += received
                        images += 1
                    except DownloadCancelled:
                        raise
                    except Exception as exc:
                        self._error(exc, f"{book['title']} / {state['chapter']} / 第 {page} 页")
            except DownloadCancelled:
                raise
            except Exception as exc:
                self._error(exc, f"{book['title']} / {state['chapter']}")
        root = self.paths.resolve(config["download_dir"])
        if state["failed"] == failed:
            self.save_local_status(root, album_id, "downloaded", book["title"], self.paths.album(config, book), images)
            await asyncio.to_thread(self.remember_complete, album_id, config, book, files)
            await asyncio.to_thread(self.store.refresh_book_metadata, self.source, album_id, book)
        else:
            message = state["errors"][errors]["message"] if len(state["errors"]) > errors else "部分图片同步失败"
            self.save_local_status(root, album_id, "error", book["title"], self.paths.album(config, book),
                                   message=f"同步未完成：{message}")

    def _target_conflict(self, path, album_id, chapter_id, page):
        """Whether ``path`` holds a file this page's checkpoints do not own; such files are never overwritten."""
        with self.store.connect() as db:
            owned = db.execute(f"SELECT 1 FROM {self.files_table} WHERE path=? AND album_id=? AND chapter_id=? AND page=?",
                               (str(path), album_id, chapter_id, page)).fetchone() is not None
        return path.exists() and not owned, owned

    def _image(self, config, album_id, chapter_id, page, url, directory):
        scope = self.output_scope(config)
        key = urlparse(url).path
        files = self.files_table
        with self.store.connect() as db:
            row = db.execute(f"SELECT * FROM {files} WHERE scope=? AND album_id=? AND chapter_id=? AND page=?",
                             (scope, album_id, chapter_id, page)).fetchone()
        if row and row["image_key"] == key:
            saved = self.paths.resolve(row["path"])
            if saved.parent == directory and saved.is_file() and saved.stat().st_size == row["size"] and file_digest(saved, self.signal) == row["digest"]:
                return True, 0, self.file_stamp(saved)
        root = self.paths.resolve(config["download_dir"])
        with self.store.connect() as db:
            local = db.execute(f"SELECT * FROM {self.local_table} WHERE root=? AND album_id=? AND chapter_id=? AND page=?",
                               (str(root), album_id, chapter_id, page)).fetchone()
        if local_file_valid(self.paths, root, local, url, self.signal):
            return True, 0, self.file_stamp(self.paths.resolve(local["path"]))
        # The extension is only known after decoding; a fixed-format target can be refused before any traffic.
        if config["image_format"] != "original":
            extension = "jpg" if config["image_format"] == "jpeg" else config["image_format"]
            if self._target_conflict(self.paths.resolve(directory / f"{page:05d}.{extension}"), album_id, chapter_id, page)[0]:
                raise ComicApiError("目标图片已存在但不属于同步记录，未覆盖", 409, "file_conflict", self.source)
        data, extension, received = receive_image(self.service.source(self.source), url, chapter_id, config, self.signal, self.source)
        if self.signal.is_set():
            raise DownloadCancelled()
        directory = self.paths.prepare(directory)
        path = self.paths.resolve(directory / f"{page:05d}.{extension}")
        conflict, owned = self._target_conflict(path, album_id, chapter_id, page)
        if conflict:
            raise ComicApiError("目标图片已存在但不属于同步记录，未覆盖", 409, "file_conflict", self.source)
        with self.store.connect() as db:
            # A pending checkpoint authorizes recovery if the process exits after the rename.
            db.execute(f"DELETE FROM {files} WHERE path=?", (str(path),))
            db.execute(f"INSERT OR REPLACE INTO {files} VALUES (?,?,?,?,?,?,0,'')",
                       (scope, album_id, chapter_id, page, key, str(path)))
        write_atomic(path, data, replace=owned)
        with self.store.connect() as db:
            db.execute(f"UPDATE {files} SET size=?, digest=? WHERE scope=? AND album_id=? AND chapter_id=? AND page=?",
                       (len(data), hashlib.sha256(data).hexdigest(), scope, album_id, chapter_id, page))
        return False, received, self.file_stamp(path)

    async def _repair_album(self, album_id, config, identity, local):
        """Keep decodable pages, move unusable files into the run's backup folder, download only the gaps."""
        state = self.current
        state.update(title=album_id, chapter="", page=0, pages=0)
        with self.store.connect() as db:
            row = db.execute(f"SELECT * FROM {self.status_table} WHERE root=? AND album_id=?", (str(local.root), album_id)).fetchone()
        book = await self.service.get_comic_detail(self.source, album_id)
        state["title"] = book["title"]
        directory = None
        try:
            directory = await asyncio.to_thread(self._repair_directory, local, row["path"] if row else "")
            chapters = book.get("chapters", [])
            if not chapters:
                raise ComicApiError("漫画暂无有效章节", 502, "invalid_response", self.source)
            prepared = []
            for chapter in chapters:
                self._check(identity)
                state.update(chapter=chapter.get("name", ""), page=0, pages=0)
                urls, chapter = await self._chapter_data(album_id, chapter)
                if not urls:
                    raise ComicApiError("章节未返回图片", 502, "invalid_response", self.source)
                prepared.append((urls, chapter))
            targets = await asyncio.to_thread(self._repair_chapter_directories, local, config, book, prepared, directory)
            for (urls, chapter), target in zip(prepared, targets):
                self._check(identity)
                state.update(chapter=chapter.get("name", ""), page=0, pages=len(urls))
                chapter_id = self.chapter_id(chapter["id"])
                await asyncio.to_thread(self._repair_pages, local, config, album_id, chapter_id, target, urls, identity)
            state.update(chapter="", page=0, pages=0)
            await self._verify_local_album(album_id, config, identity, local, book, directory, prepared)
            state["repaired_albums"] += 1
        except DownloadCancelled:
            raise
        except Exception as exc:
            if self._local_failure(exc):
                self.save_local_status(local.root, album_id, "error", book["title"], directory or (row["path"] if row else ""),
                                       message=self._message(exc))
            raise

    def _repair_directory(self, local, saved):
        # Only a directory the check uniquely matched is repaired; ambiguous matches need manual cleanup.
        try:
            path = self.paths.resolve(saved) if saved else None
        except ComicApiError:
            path = None
        if not path or not path.is_dir() or path == local.root or not path.is_relative_to(local.root):
            raise ComicApiError("本地漫画目录未唯一匹配或已不存在，需整理目录后重新检查", 409, "local_unmatched", self.source)
        return path

    def _repair_chapter_directories(self, local, config, book, prepared, album):
        """Resolve every chapter's directory: exact match, then a unique same-title directory whose
        episode number shifted (renamed in place), else a new directory for a full download."""
        found = [local.find_chapter(album, chapter, index) for index, (_, chapter) in enumerate(prepared, 1)]
        matched = [path for path in found if path]
        if len(set(matched)) != len(matched):
            raise ComicApiError("多个章节匹配到同一目录，未自动修复", 409, "local_ambiguous", self.source)
        names = [self.paths.chapter(config, book, chapter, index).name for index, (_, chapter) in enumerate(prepared, 1)]
        used, spare = set(matched), {}
        for path in local.children(album):
            title = local.episode_title(path.name)
            if path not in used and path.name not in names and title is not None:
                spare.setdefault(title, []).append(path)
        wanted = {}
        for index, (path, name) in enumerate(zip(found, names)):
            if not path and local.episode_title(name) is not None:
                wanted.setdefault(local.episode_title(name), []).append(index)
        targets = list(found)
        for title, indexes in wanted.items():
            candidates = spare.get(title, [])
            if len(indexes) == 1 and len(candidates) == 1:
                index, source = indexes[0], candidates[0]
                target = self.paths.resolve(album / names[index])
                chapter_id = self.chapter_id(prepared[index][1]["id"])
                if target.exists() or self._recorded_elsewhere(source, book["id"], chapter_id):
                    continue
                os.rename(source, target)
                self._move_records(source, target)
                self.current["renamed_chapters"] += 1
                if len(self.current["renames"]) < 100:
                    self.current["renames"].append({"title": book["title"], "from": source.name, "to": target.name})
                targets[index] = target
        for index, path in enumerate(targets):
            if path:
                continue
            target = self.paths.resolve(album / names[index])
            if target.exists() and not target.is_dir():
                raise ComicApiError(f"章节目录名称被文件占用：{names[index]}", 409, "file_conflict", self.source)
            target.mkdir(exist_ok=True)
            targets[index] = target
        local.directories.pop(self.paths.resolve(album), None)
        return targets

    def _recorded_elsewhere(self, directory, album_id, chapter_id):
        """Sync or check records proving the directory holds another chapter veto a title-based rename."""
        prefix = str(directory) + os.sep
        with self.store.connect() as db:
            return any(db.execute(f"""SELECT 1 FROM {table} WHERE substr(path, 1, ?)=? AND (album_id<>? OR chapter_id<>?)
                LIMIT 1""", (len(prefix), prefix, self.comic_id(album_id), chapter_id)).fetchone()
                       for table in (self.files_table, self.local_table))

    def _move_records(self, source, target):
        """Checkpoints follow a renamed chapter directory so its files keep their verified baseline."""
        prefix, moved = str(source) + os.sep, str(target) + os.sep
        with self.store.connect() as db:
            for table in (self.files_table, self.local_table):
                # Rows for the previously absent target directory are stale.
                db.execute(f"DELETE FROM {table} WHERE substr(path, 1, ?)=?", (len(moved), moved))
                db.execute(f"UPDATE {table} SET path=? || substr(path, ?) WHERE substr(path, 1, ?)=?",
                           (moved, len(prefix) + 1, len(prefix), prefix))

    def _repair_pages(self, local, config, album_id, chapter_id, directory, urls, identity):
        state = self.current
        files = local.image_files(directory)
        expected = {Path(unquote(urlparse(url).path)).stem: page for page, url in enumerate(urls, 1)}
        numeric = [p for p in files if re.fullmatch(r"[0-9]{1,20}", p.stem) and 1 <= int(p.stem) <= len(urls)]
        named = [p for p in files if p.stem in expected]
        # Pages are numbered 1..N or named after the source files, whichever explains more existing files.
        by_stem = len(named) > len(numeric)
        slots, extras = {}, []
        for path in sorted(files, key=lambda p: p.name):
            if path in (named if by_stem else numeric):
                slots.setdefault(expected[path.stem] if by_stem else int(path.stem), []).append(path)
            else:
                extras.append(path)
        padded = [p.stem for p in numeric if p.stem.startswith("0")]
        width = max(map(len, padded)) if padded else 0 if numeric else 5
        for page, url in enumerate(urls, 1):
            self._check(identity)
            state["page"] = page
            kept = None
            for path in slots.get(page, []):
                if kept is None:
                    try:
                        record = local.inspect(path)
                    except ComicApiError:
                        pass
                    else:
                        kept = path
                        self._forget_stale(album_id, chapter_id, page, url, record)
                        continue
                self._move_to_backup(local, path)
            if kept is not None:
                continue
            data, extension, received = receive_image(self.service.source(self.source), url, chapter_id, config, self.signal, self.source)
            self._check(identity)
            stem = Path(unquote(urlparse(url).path)).stem if by_stem else str(page).zfill(width)
            path = self.paths.resolve(directory / f"{stem}.{extension}")
            if path.exists():
                self._move_to_backup(local, path)
            write_atomic(path, data)
            state["downloaded"] += 1
            state["received_bytes"] += received
        for path in extras:
            self._move_to_backup(local, path)

    def _forget_stale(self, album_id, chapter_id, page, url, record):
        """A kept, decodable file becomes the new baseline; this album's outdated records for it are dropped."""
        with self.store.connect() as db:
            for table in (self.files_table, self.local_table):
                db.execute(f"""DELETE FROM {table} WHERE path=? AND album_id=? AND NOT (chapter_id=? AND page=?
                    AND image_key=? AND size=? AND digest=?)""",
                           (record["path"], album_id, chapter_id, page, urlparse(url).path, record["size"], record["digest"]))

    def _move_to_backup(self, local, path):
        target = self.paths.resolve(self.current["backup_dir"]) / path.relative_to(local.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ComicApiError(f"备份位置已存在同名文件，未移动：{path.name}", 409, "file_conflict", self.source)
        os.rename(path, target)
        with self.store.connect() as db:
            for table in (self.files_table, self.local_table):
                db.execute(f"DELETE FROM {table} WHERE path=?", (str(path),))
        self.current["moved_images"] += 1
