"""Temporary: move images downloaded by the removed JM/Bika favorites sync into the library save directory.

Every comic the old sync recorded as complete is moved to the path the current library rule gives it
under ``<library save dir>/<source>``, registered as a checked local copy and added to the library.
A preview lists what will happen first; nothing is overwritten and incomplete copies stay where they are.
Remove this module, its routes and UI, and the old jm_/bika_ sync tables in the next version.
"""
import asyncio
import hashlib
import logging
import os
import shutil
import time
from pathlib import Path

from src.errors import ComicApiError
from src.services.local_images import file_digest

log = logging.getLogger("comic.legacy_migration")
SOURCES = ("jm", "bika")
STATE_KEY = "legacy_migration"
OLD_SETTINGS = {"jm": ("jm_sync", "JM_DOWNLOAD_ROOTS", "jm-favorites"), "bika": ("bika_sync", "BIKA_DOWNLOAD_ROOTS", "bika-favorites")}


class LegacyMigration:
    def __init__(self, store, service, library):
        self.store, self.service, self.library = store, service, library
        self.task = None
        # Album moves of the latest preview, keyed by (source, comic ID); kept in memory only.
        self.plan = {}
        self.state = store.settings(STATE_KEY) or {"status": "idle"}
        if self.state.get("status") in ("previewing", "running"):
            self.state.update(status="interrupted", error="服务重启中断；已迁移的漫画保留，需重新预览")
            self._persist()

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def _persist(self):
        self.store.save_settings(STATE_KEY, self.state)

    def pending(self):
        """Comics the old sync recorded as complete and that have not been migrated yet."""
        total = 0
        with self.store.connect() as db:
            for source in SOURCES:
                total += db.execute(f"SELECT COUNT(*) FROM {source}_local_status WHERE status='downloaded'").fetchone()[0]
        return total

    def status(self):
        return {**self.state, "active": self.active, "pending": self.pending()}

    def _guard(self):
        if self.active:
            raise ComicApiError("迁移任务正在运行", 409, "migration_active", "library")
        if self.library.active:
            raise ComicApiError("书库同步进行中，停止后才能迁移", 409, "sync_active", "library")

    def preview(self):
        self._guard()
        self.plan = {}
        self.state = {"status": "previewing", "started_at": time.time(), "finished_at": None, "scope": self._scope(),
                      "albums_total": self.pending(), "albums_done": 0, "albums": [], "leftovers": [], "error": ""}
        self._persist()
        self.task = asyncio.create_task(self._preview())
        return self.status()

    def run(self):
        self._guard()
        if self.state.get("status") != "ready" or not self.plan:
            raise ComicApiError("需先生成迁移预览", 409, "preview_required", "library")
        if self.state.get("scope") != self._scope():
            raise ComicApiError("书库同步设置已变化，需重新预览", 409, "preview_stale", "library")
        self.state.update(status="running", started_at=time.time(), finished_at=None, albums_done=0, error="")
        self._persist()
        self.library.blocked_by = "旧同步目录正在迁移，完成后才能同步书库"
        self.task = asyncio.create_task(self._run())
        return self.status()

    def _scope(self):
        config = self.library.settings()
        return [config["download_dir"], config["directory_rule"]]

    def _albums(self, source):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(
                f"SELECT root, album_id, title, path FROM {source}_local_status WHERE status='downloaded' ORDER BY checked_at")]

    def _files(self, source, row):
        """One existing file per page inside the recorded comic directory, from downloads and checked imports."""
        album_dir = Path(row["path"])
        with self.store.connect() as db:
            rows = [*db.execute(f"SELECT chapter_id, page, image_key, path, size FROM {source}_sync_files WHERE album_id=?",
                                (row["album_id"],)),
                    *db.execute(f"SELECT chapter_id, page, image_key, path, size FROM {source}_sync_local_files WHERE album_id=? AND root=?",
                                (row["album_id"], row["root"]))]
        pages = {}
        for item in rows:
            path = Path(item["path"])
            try:
                stat = path.stat()
            except OSError:
                continue
            if not path.is_relative_to(album_dir) or not path.is_file() or item["size"] <= 0 or stat.st_size != item["size"]:
                continue
            key = (item["chapter_id"], item["page"])
            # Format changes can leave one page in several formats; the newest file is the current one.
            if key not in pages or stat.st_mtime_ns > pages[key][1]:
                pages[key] = (dict(item), stat.st_mtime_ns)
        return [item for item, _ in pages.values()]

    def _targets(self, worker, album_id, book, files):
        config = worker.settings()
        chapters = {}
        for index, chapter in enumerate(book.get("chapters", []), 1):
            chapters[worker.chapter_id(chapter["id"])] = (index, chapter)
        moves = []
        for item in files:
            if item["chapter_id"] not in chapters:
                continue
            index, chapter = chapters[item["chapter_id"]]
            directory = worker.paths.chapter(config, book, chapter, index)
            target = worker.paths.resolve(directory / f"{item['page']:05d}{Path(item['path']).suffix.lower()}")
            if target.exists() and not os.path.samefile(target, item["path"]):
                raise ComicApiError(f"目标位置已有其他文件：{target}", 409, "file_conflict", worker.source)
            moves.append(dict(item, target=str(target)))
        if not moves:
            raise ComicApiError("已登记图片与当前章节均不对应", 409, "local_incomplete", worker.source)
        if config["directory_rule"] == "title_plain":
            worker._guard_plain_directory(album_id, worker.paths.album(config, book))
        complete = set(chapters) <= {item["chapter_id"] for item in moves}
        return moves, complete, str(worker.paths.album(config, book))

    async def _preview(self):
        state = self.state
        try:
            for source in SOURCES:
                for row in await asyncio.to_thread(self._albums, source):
                    entry = {"source": source, "id": row["album_id"], "title": row["title"] or row["album_id"],
                             "from": row["path"], "to": "", "images": 0, "status": "failed", "message": ""}
                    try:
                        worker = self.library.worker(source)
                        album_id = entry["id"] = worker.normalize_id(row["album_id"])
                        status = await asyncio.to_thread(worker.local_status, [album_id])
                        if status["items"].get(album_id, {}).get("status") == "downloaded":
                            entry.update(status="skipped", message="书库已有完整副本，旧文件保留在原处")
                        else:
                            files = await asyncio.to_thread(self._files, source, row)
                            if not files:
                                raise ComicApiError("旧目录中没有可迁移的已登记图片", 409, "local_incomplete", source)
                            book = await self.service.get_comic_detail(source, album_id)
                            moves, complete, target = await asyncio.to_thread(self._targets, worker, album_id, book, files)
                            entry.update(title=book["title"], to=target, images=len(moves), status="ready",
                                         message="" if complete else "部分章节未下载，迁移后由书库同步补齐")
                            self.plan[(source, album_id)] = {"row": row, "book": book, "moves": moves, "complete": complete}
                    except Exception as exc:
                        entry["message"] = str(exc) if isinstance(exc, ComicApiError) else "预览失败"
                        if not isinstance(exc, ComicApiError):
                            log.warning("Legacy migration preview failed for %s/%s", source, row["album_id"], exc_info=True)
                    state["albums"].append(entry)
                    state["albums_done"] += 1
            state["status"] = "ready" if self.plan else "completed"
        except Exception:
            state.update(status="failed", error="迁移预览失败")
            log.warning("Legacy migration preview failed", exc_info=True)
        finally:
            state["finished_at"] = time.time()
            planned = {Path(plan["row"]["path"]) for plan in self.plan.values()}
            state["leftovers"] = await asyncio.to_thread(self._leftovers, planned)
            self._persist()

    async def _run(self):
        state = self.state
        entries = {(entry["source"], entry["id"]): entry for entry in state["albums"]}
        try:
            for key, plan in list(self.plan.items()):
                entry = entries[key]
                try:
                    await asyncio.to_thread(self._migrate, key[0], key[1], plan)
                    entry["status"] = "migrated"
                except Exception as exc:
                    entry.update(status="failed", message=str(exc) if isinstance(exc, ComicApiError) else "迁移失败，旧文件保持原样")
                    if not isinstance(exc, ComicApiError):
                        log.warning("Legacy migration failed for %s/%s", *key, exc_info=True)
                self.plan.pop(key)
                state["albums_done"] += 1
                self._persist()
            state["status"] = "completed"
        except Exception:
            state.update(status="failed", error="迁移失败")
            log.warning("Legacy migration failed", exc_info=True)
        finally:
            self.library.blocked_by = None
            state["finished_at"] = time.time()
            state["leftovers"] = await asyncio.to_thread(self._leftovers)
            self._persist()

    def _migrate(self, source, album_id, plan):
        worker = self.library.worker(source)
        config = worker.settings()
        root = worker.paths.resolve(config["download_dir"])
        row, book, moves = plan["row"], plan["book"], plan["moves"]
        for item in moves:
            path, target = Path(item["path"]), Path(item["target"])
            if not path.is_file() or path.stat().st_size != item["size"]:
                raise ComicApiError("旧图片在预览后发生变化，需重新预览", 409, "local_changed", source)
            if target.exists() and not os.path.samefile(target, path):
                raise ComicApiError(f"目标位置已有其他文件：{target}", 409, "file_conflict", source)
        moved = []
        try:
            for item in moves:
                path, target = Path(item["path"]), Path(item["target"])
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(path, target)
                moved.append((path, target))
        except Exception:
            # Put back what this album already moved so it can be previewed and migrated again.
            for path, target in reversed(moved):
                shutil.move(target, path)
            raise
        records = [(str(root), album_id, item["chapter_id"], item["page"], item["image_key"], item["target"],
                    Path(item["target"]).stat().st_size, file_digest(Path(item["target"]))) for item in moves]
        old_key = f"image_sync_complete:{source}:{hashlib.sha256(row['root'].encode()).hexdigest()[:16]}:{row['album_id']}"
        with self.store.connect() as db:
            db.executemany(f"INSERT OR REPLACE INTO {worker.local_table} VALUES (?,?,?,?,?,?,?,?)", records)
            for table in (f"{source}_sync_files", f"{source}_sync_local_files", f"{source}_local_status"):
                db.execute(f"DELETE FROM {table} WHERE album_id=?", (row["album_id"],))
            db.execute("DELETE FROM app_settings WHERE name=?", (old_key,))
            in_library = db.execute("SELECT 1 FROM library WHERE source=? AND comic_id=?", (source, album_id)).fetchone()
        if not in_library:
            self.library._add_book(source, album_id, book)
        if plan["complete"]:
            album_dir = worker.paths.album(config, book)
            worker.save_local_status(root, album_id, "downloaded", book["title"], album_dir, len(records))
            worker.remember_complete(album_id, config, book, [{"path": item["target"]} for item in moves])
        self._prune(Path(row["root"]), {Path(item["path"]).parent for item in moves})

    @staticmethod
    def _prune(root, directories):
        """Remove directories the move left empty, up to the old save directory."""
        for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            while directory != root and directory.is_relative_to(root):
                try:
                    directory.rmdir()
                except OSError:
                    break
                directory = directory.parent

    def _old_roots(self):
        roots = set()
        for source, (key, env, default) in OLD_SETTINGS.items():
            saved = self.store.settings(key).get("download_dir")
            configured = [part.strip() for part in os.environ.get(env, "").split(os.pathsep) if part.strip()]
            for value in [saved, *configured, str(self.store.root / default)]:
                if value:
                    roots.add(Path(value).expanduser())
        with self.store.connect() as db:
            for source in SOURCES:
                roots.update(Path(row[0]) for row in db.execute(f"SELECT DISTINCT root FROM {source}_local_status"))
        return sorted(roots)

    def _leftovers(self, planned=()):
        """Entries inside the old save directories that migration does not move (``planned`` ones will be)."""
        result = []
        for root in self._old_roots():
            try:
                names = sorted(path.name for path in root.iterdir() if not path.name.startswith(".") and path not in planned)
            except OSError:
                continue
            if names:
                result.append({"root": str(root), "entries": names[:50], "more": max(0, len(names) - 50)})
        return result
