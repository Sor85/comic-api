"""Single-instance, additive JM favorites sync with durable per-image checkpoints."""
import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.errors import ComicApiError, DownloadCancelled
from src.services.jm_local import LocalLibrary, local_file_valid
from src.services.jm_naming import JM_DIRECTORY_RULE
from src.services.local_images import (
    DIRECTORY_RULES, FORMATS, DownloadPaths, file_digest, numeric_id, receive_image, write_atomic,
)

log = logging.getLogger("comic.jm_sync")


class SyncSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    interval_minutes: int = Field(default=60, ge=5, le=10080)
    folder_ids: list[str] = Field(default_factory=list, max_length=100)
    download_dir: str = Field(default="", max_length=2048)
    speed_limit_kbps: int = Field(default=0, ge=0, le=1048576)
    image_format: Literal["original", "jpeg", "png", "webp"] = "original"
    quality: int = Field(default=90, ge=1, le=100)
    directory_rule: Literal["id", "title", "title_plain", "author", "Bd_Aauthoroname_Pindextitle"] = "title"
    retries: int = Field(default=2, ge=0, le=5)
    add_to_library: bool = True

    @field_validator("folder_ids")
    @classmethod
    def valid_folders(cls, values):
        if any(not value.isascii() or not value.isdigit() or len(value) > 20 for value in values):
            raise ValueError("收藏夹编号无效")
        return list(dict.fromkeys(values))


class JmSync:
    def __init__(self, store, service):
        self.store, self.service = store, service
        self.paths = DownloadPaths(store)
        self.task = self.scheduler = None
        self.signal = threading.Event()
        self.wake = asyncio.Event()
        self.stopping = False
        self.account_changing = False
        self.current = None
        self.next_run_at = store.settings("jm_sync_clock").get("next_run_at", 0)

    def settings(self):
        saved = self.store.settings("jm_sync")
        values = {key: value for key, value in saved.items() if key in SyncSettings.model_fields}
        config = SyncSettings(**values).model_dump()
        config["download_dir"] = config["download_dir"] or str(self.paths.roots[0])
        return config

    def options(self):
        return {"settings": self.settings(), "roots": [str(root) for root in self.paths.roots],
                "directory_rules": [{"value": key, "label": value} for key, value in DIRECTORY_RULES.items()],
                "formats": [{"value": key, "label": value} for key, value in FORMATS.items()]}

    def identity(self):
        account = self.store.account("jm") or {}
        return hashlib.sha256(str(account.get("account", "")).encode()).hexdigest()

    def require_account(self):
        if self.account_changing:
            raise ComicApiError("禁漫账号正在变更", 409, "account_changing", "jm")
        plugin = self.service.source("jm")
        if "favorites" not in plugin.capabilities:
            raise ComicApiError("当前禁漫插件不支持收藏夹", 400, "unsupported", "jm")
        status = plugin.account_status()
        if not (status.get("authenticated") or status.get("configured")):
            raise ComicApiError("禁漫收藏需要绑定账号", 401, "login_required", "jm")
        return plugin

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def guard_account_change(self):
        if self.active or self.account_changing:
            raise ComicApiError("收藏同步或账号请求进行中，停止后才能变更禁漫账号", 409, "sync_active", "jm")
        self.account_changing = True

    def account_changed(self):
        config = self.store.settings("jm_sync")
        config["enabled"] = False
        config.pop("owner", None)
        self.store.save_settings("jm_sync", config)
        self.wake.set()

    def save_settings(self, config):
        if self.active:
            raise ComicApiError("收藏同步进行中，停止后才能修改设置", 409, "sync_active", "jm")
        values = config.model_dump()
        values["download_dir"] = str(self.paths.resolve(values["download_dir"] or self.paths.roots[0]))
        if values["enabled"]:
            self.require_account()
        values["owner"] = self.identity()
        self.store.save_settings("jm_sync", values)
        self.next_run_at = 0
        self.store.save_settings("jm_sync_clock", {"next_run_at": 0})
        self.wake.set()
        return self.options()

    def _persist(self, state):
        with self.store.connect() as db:
            db.execute("INSERT INTO jm_sync_runs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state",
                       (state["id"], json.dumps(state, ensure_ascii=False)))
            db.execute("DELETE FROM jm_sync_runs WHERE rowid NOT IN (SELECT rowid FROM jm_sync_runs ORDER BY rowid DESC LIMIT 30)")

    def history(self):
        with self.store.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT state FROM jm_sync_runs ORDER BY rowid DESC LIMIT 10")]

    def status(self):
        return {"active": self.active, "current": self.current, "history": self.history(),
                "next_run_at": self.next_run_at if self.settings()["enabled"] and not self.active else None}

    async def start(self):
        for state in self.history():
            if state["status"] in ("running", "cancelling"):
                state.update(status="interrupted", finished_at=time.time(), error="服务重启中断；已完成图片保留，再次同步会继续")
                self._persist(state)
        self.scheduler = asyncio.create_task(self._schedule())

    async def stop(self):
        self.stopping = True
        self.signal.set()
        self.wake.set()
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

    def _schedule_next(self):
        self.next_run_at = time.time() + self.settings()["interval_minutes"] * 60
        self.store.save_settings("jm_sync_clock", {"next_run_at": self.next_run_at})

    @staticmethod
    def _new_state(automatic, check_local=False):
        return {"id": uuid.uuid4().hex, "automatic": automatic, "mode": "check_local" if check_local else "sync",
                "status": "running", "stage": "scanning",
                "started_at": time.time(), "finished_at": None, "albums_total": 0, "albums_done": 0,
                "downloaded": 0, "skipped": 0, "failed": 0, "received_bytes": 0,
                "local_albums": 0, "local_images": 0, "local_missing": 0, "local_results": [],
                "title": "", "chapter": "", "page": 0, "pages": 0, "errors": [], "error": ""}

    def launch(self, album_ids=None, automatic=False, check_local=False):
        if self.active:
            raise ComicApiError("已有收藏同步任务正在运行", 409, "sync_active", "jm")
        self.require_account()
        if automatic and self.store.settings("jm_sync").get("owner") != self.identity():
            self.account_changed()
            raise ComicApiError("禁漫账号已变更，自动同步已关闭，需重新保存设置", 409, "account_changed", "jm")
        config = self.settings()
        self.paths.resolve(config["download_dir"])
        self.signal = threading.Event()
        self.current = self._new_state(automatic, check_local)
        self.current["settings"] = config
        self._persist(self.current)
        self.task = asyncio.create_task(self._run(config, album_ids, self.identity()))
        return dict(self.current)

    def cancel(self):
        if not self.active:
            raise ComicApiError("没有正在运行的收藏同步任务", 409, "invalid_state", "jm")
        self.signal.set()
        self.current["status"] = "cancelling"
        self._persist(self.current)
        return dict(self.current)

    def _check(self, identity):
        if self.signal.is_set():
            raise DownloadCancelled()
        if identity != self.identity():
            raise ComicApiError("同步期间禁漫账号发生变更", 409, "account_changed", "jm")

    def _error(self, exc, title=""):
        self.current["failed"] += 1
        message = str(exc) if isinstance(exc, ComicApiError) else "本地写入或图源请求失败"
        if len(self.current["errors"]) < 100:
            self.current["errors"].append({"title": title, "message": message})
        log.warning("JM sync item failed: %s", message, exc_info=not isinstance(exc, ComicApiError))
        if isinstance(exc, ComicApiError) and (exc.status_code in (401, 403) or exc.code == "account_changed"):
            raise exc

    async def _collect(self, config, identity):
        result = {}
        for folder_id in config["folder_ids"] or ["0"]:
            previous_pages, folder_albums = set(), set()
            expected_total = None
            for page in range(1, 1001):
                self._check(identity)
                data = await self.service.call("jm", "favorites", page=page, folder_id=folder_id, order="mr")
                signature = tuple(str(item["id"]) for item in data["items"])
                if signature and signature in previous_pages:
                    raise ComicApiError("收藏分页重复，已停止扫描，避免重复下载", 502, "invalid_response", "jm")
                previous_pages.add(signature)
                total = data.get("total")
                if expected_total is None:
                    expected_total = total
                elif total != expected_total:
                    raise ComicApiError("收藏在扫描期间发生变化，需要重新同步", 409, "favorites_changed", "jm")
                for item in data["items"]:
                    album_id = numeric_id(item["id"])
                    folder_albums.add(album_id)
                    result[album_id] = item
                self.current["albums_total"] = len(result)
                self._persist(self.current)
                if not data["has_next"]:
                    if expected_total is not None and len(folder_albums) != expected_total:
                        raise ComicApiError("收藏分页数量不一致，需要重新同步", 502, "invalid_response", "jm")
                    break
                if not signature:
                    raise ComicApiError("收藏分页提前返回空页", 502, "invalid_response", "jm")
            else:
                raise ComicApiError("收藏超过 1000 页扫描上限，需缩小收藏夹范围", 422, "sync_too_large", "jm")
        return result

    async def _run(self, config, album_ids, identity):
        state = self.current
        checking = state["mode"] == "check_local"
        try:
            if not checking:
                await asyncio.to_thread(self.paths.prepare, config["download_dir"])
            favorites = await self._collect(config, identity) if checking or not album_ids else {}
            ids = list(favorites) if checking or not album_ids else list(dict.fromkeys(album_ids))
            state["albums_total"] = len(ids)
            local = None
            if checking:
                if config["directory_rule"] == JM_DIRECTORY_RULE:
                    # Canonical names can collapse different translated titles. Resolve every
                    # author's actual metadata before allowing any album to be registered.
                    state["stage"] = "matching"
                    favorites = {}
                    for album_id in ids:
                        self._check(identity)
                        state["title"] = album_id
                        self._persist(state)
                        favorites[album_id] = await self.service.get_comic_detail("jm", album_id)
                local = await asyncio.to_thread(LocalLibrary, self.store, self.paths, config, favorites.values(), self.signal)
            state["stage"] = "checking" if checking else "downloading"
            for album_id in ids:
                self._check(identity)
                try:
                    if checking:
                        await self._check_local_album(numeric_id(album_id), config, identity, local)
                    else:
                        await self._album(numeric_id(album_id), config, identity)
                except DownloadCancelled:
                    raise
                except Exception as exc:
                    label = state["title"] or album_id
                    if checking and state["chapter"]:
                        label += f" / {state['chapter']}"
                    self._error(exc, label)
                state["albums_done"] += 1
                self._persist(state)
            self._check(identity)
            state["status"] = "partial" if state["failed"] else "completed"
        except DownloadCancelled:
            state["status"] = "interrupted" if self.stopping else "cancelled"
        except Exception as exc:
            state.update(status="failed", error=str(exc) if isinstance(exc, ComicApiError) else "同步失败，需检查目录权限和图源状态")
            log.warning("JM sync failed", exc_info=True)
        finally:
            state.update(finished_at=time.time(), stage="finished")
            self._persist(state)
            if not checking:
                self._schedule_next()
            self.wake.set()

    def _save_book(self, album_id, book):
        with self.store.connect() as db:
            row = db.execute("SELECT category FROM library WHERE source='jm' AND comic_id=?", (album_id,)).fetchone()
        self.store.save_book("jm", album_id, book, row[0] if row else "JM 收藏")

    async def _chapter_data(self, config, album_id, chapter):
        chapter_id = numeric_id(chapter["id"])
        if config["directory_rule"] != JM_DIRECTORY_RULE:
            return await self.service.get_chapter_images("jm", album_id, chapter_id), chapter
        if not hasattr(self.service.source("jm"), "chapter_detail"):
            raise ComicApiError("当前禁漫插件不提供 Pindextitle 所需的原始章节信息", 400, "unsupported", "jm")
        data = await self.service.call("jm", "chapter_detail", album_id, chapter_id)
        if (not isinstance(data, dict) or not {"id", "name", "sort", "series_id", "images"} <= data.keys()
                or int(numeric_id(data["id"])) != int(chapter_id) or not isinstance(data["images"], list)):
            raise ComicApiError("禁漫章节信息不完整或编号不一致", 502, "invalid_response", "jm")
        return data["images"], dict(chapter, name=data["name"], sort=data["sort"], series_id=data["series_id"])

    async def _check_local_album(self, album_id, config, identity, local):
        state = self.current
        state.update(title=album_id, chapter="", page=0, pages=0)
        book = local.books[album_id] if config["directory_rule"] == JM_DIRECTORY_RULE else await self.service.get_comic_detail("jm", album_id)
        state["title"] = book["title"]
        directory = await asyncio.to_thread(local.album, book)
        if not directory:
            state["local_missing"] += 1
            return
        chapters = book.get("chapters", [])
        if not chapters:
            raise ComicApiError("漫画暂无有效章节", 502, "invalid_response", "jm")
        records, used = [], set()
        for index, chapter in enumerate(chapters, 1):
            self._check(identity)
            state.update(chapter=chapter.get("name", ""), page=0, pages=0)
            chapter_id = numeric_id(chapter["id"])
            urls, chapter = await self._chapter_data(config, album_id, chapter)
            if not urls:
                raise ComicApiError("章节未返回图片", 502, "invalid_response", "jm")
            state["chapter"] = chapter.get("name", "")
            target = await asyncio.to_thread(local.chapter, directory, chapter, index)
            if target in used:
                raise ComicApiError("多个章节匹配到同一目录，未认定为已下载", 409, "local_ambiguous", "jm")
            used.add(target)
            state["pages"] = len(urls)
            self._persist(state)
            records.extend(await asyncio.to_thread(local.validate_chapter, album_id, chapter_id, target, urls))
            state["page"] = len(urls)
        self._check(identity)
        await asyncio.to_thread(local.remember, album_id, records)
        state["local_albums"] += 1
        state["local_images"] += len(records)
        if len(state["local_results"]) < 100:
            state["local_results"].append({"id": album_id, "title": book["title"], "path": str(directory), "images": len(records)})
        if config["add_to_library"]:
            self._save_book(album_id, book)

    def _guard_plain_directory(self, album_id, directory):
        prefix = str(directory) + os.sep
        with self.store.connect() as db:
            for table in ("jm_sync_files", "jm_sync_local_files"):
                conflict = db.execute(f"SELECT 1 FROM {table} WHERE album_id<>? AND substr(path,1,?)=? COLLATE NOCASE LIMIT 1",
                                      (album_id, len(prefix), prefix)).fetchone()
                if conflict:
                    raise ComicApiError("无 ID 漫画目录已关联其他漫画，未混合写入；需改用带 ID 规则", 409, "file_conflict", "jm")

    async def _album(self, album_id, config, identity):
        state = self.current
        state.update(title=album_id, chapter="", page=0, pages=0)
        book = await self.service.get_comic_detail("jm", album_id)
        state["title"] = book["title"]
        if config["add_to_library"]:
            self._save_book(album_id, book)
        chapters = book.get("chapters", [])
        if not chapters:
            raise ComicApiError("漫画暂无有效章节", 502, "invalid_response", "jm")
        if config["directory_rule"] in ("title_plain", JM_DIRECTORY_RULE):
            self._guard_plain_directory(album_id, self.paths.album(config, book))
        for index, chapter in enumerate(chapters, 1):
            self._check(identity)
            state.update(chapter=chapter.get("name", ""), page=0, pages=0)
            try:
                chapter_id = numeric_id(chapter["id"])
                urls, chapter = await self._chapter_data(config, album_id, chapter)
                if not urls:
                    raise ComicApiError("章节未返回图片", 502, "invalid_response", "jm")
                state.update(chapter=chapter.get("name", ""), pages=len(urls))
                directory = self.paths.chapter(config, book, chapter, index)
                for page, url in enumerate(urls, 1):
                    self._check(identity)
                    state["page"] = page
                    self._persist(state)
                    try:
                        skipped, received = await asyncio.to_thread(self._image, config, album_id, chapter_id, page, url, directory)
                        state["skipped" if skipped else "downloaded"] += 1
                        state["received_bytes"] += received
                    except DownloadCancelled:
                        raise
                    except Exception as exc:
                        self._error(exc, f"{book['title']} / {state['chapter']} / 第 {page} 页")
            except DownloadCancelled:
                raise
            except Exception as exc:
                self._error(exc, f"{book['title']} / {state['chapter']}")

    def _image(self, config, album_id, chapter_id, page, url, directory):
        scope = hashlib.sha256(json.dumps([config[key] for key in ("download_dir", "directory_rule", "image_format", "quality")]).encode()).hexdigest()
        key = urlparse(url).path
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM jm_sync_files WHERE scope=? AND album_id=? AND chapter_id=? AND page=?",
                             (scope, album_id, chapter_id, page)).fetchone()
        if row and row["image_key"] == key:
            saved = self.paths.resolve(row["path"])
            if saved.parent == directory and saved.is_file() and saved.stat().st_size == row["size"] and file_digest(saved, self.signal) == row["digest"]:
                return True, 0
        root = self.paths.resolve(config["download_dir"])
        with self.store.connect() as db:
            local = db.execute("SELECT * FROM jm_sync_local_files WHERE root=? AND album_id=? AND chapter_id=? AND page=?",
                               (str(root), album_id, chapter_id, page)).fetchone()
        if local_file_valid(self.paths, root, local, url, self.signal):
            return True, 0
        data, extension, received = receive_image(self.service.source("jm"), url, chapter_id, config, self.signal)
        if self.signal.is_set():
            raise DownloadCancelled()
        directory = self.paths.prepare(directory)
        path = self.paths.resolve(directory / f"{page:05d}.{extension}")
        with self.store.connect() as db:
            owned = db.execute("SELECT 1 FROM jm_sync_files WHERE path=? AND album_id=? AND chapter_id=? AND page=?",
                               (str(path), album_id, chapter_id, page)).fetchone() is not None
            if path.exists() and not owned:
                raise ComicApiError("目标图片已存在但不属于同步记录，未覆盖", 409, "file_conflict", "jm")
            # A pending checkpoint authorizes recovery if the process exits after the rename.
            db.execute("DELETE FROM jm_sync_files WHERE path=?", (str(path),))
            db.execute("INSERT OR REPLACE INTO jm_sync_files VALUES (?,?,?,?,?,?,0,'')",
                       (scope, album_id, chapter_id, page, key, str(path)))
        write_atomic(path, data, replace=owned)
        with self.store.connect() as db:
            db.execute("UPDATE jm_sync_files SET size=?, digest=? WHERE scope=? AND album_id=? AND chapter_id=? AND page=?",
                       (len(data), hashlib.sha256(data).hexdigest(), scope, album_id, chapter_id, page))
        return False, received
