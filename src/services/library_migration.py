"""Move comic directories of any layout into the library save directory under the current directory rule.

A chosen source directory is scanned for library books by comic ID or title in every layout the library
rules produce, the saved custom template, the directory a check or sync last recorded, plus the 【作者】原标题
layout of older JMComic-style downloads and single-chapter comics whose pages sit directly in the comic directory. A preview lists every match first; running it moves each page that
maps to a chapter page to ``<library save dir>/<source>/<current rule path>``, registers it as a checked local
copy and leaves broken, extra and unmatched files where they are. Nothing is overwritten.
"""
import asyncio
import logging
import os
import re
import shutil
import threading
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse

from src.errors import ComicApiError, DownloadCancelled
from src.services.image_sync import DEFAULT_ROOT, ROOTS_ENV
from src.services.local_check import LocalLibrary, name_keys
from src.services.local_images import DIRECTORY_RULES, DownloadPaths, id_free, parse_template

log = logging.getLogger("comic.library_migration")
STATE_KEY = "library_migration"
# Save directories of the removed JM/Bika favorites sync stay selectable as migration sources.
LEGACY_ROOTS = (("JM_DOWNLOAD_ROOTS", "jm-favorites"), ("BIKA_DOWNLOAD_ROOTS", "bika-favorites"))
BRACKET_GROUP = r"\[[^\]]*\]|\([^)]*\)|【[^】]*】|（[^）]*）"
LEADING_GROUP = re.compile(rf"^\s*(?:{BRACKET_GROUP})\s*")


def original_name(title):
    """First title token outside brackets, as JMComic-style ``【作者】原标题`` directories name a comic."""
    for token in re.split(BRACKET_GROUP, str(title)):
        if token.strip():
            return token.strip()
    return str(title)


class SourceTree(LocalLibrary):
    """Read-only matching over a migration source, which may use any directory rule."""

    def __init__(self, store, paths, root, books, owner, template="", signal=None):
        # The saved custom template is recognized even after switching back to a built-in rule.
        try:
            parse_template(template)
            rule = "custom"
        except ValueError:
            rule = ""
        super().__init__(store, paths, {"download_dir": str(root), "directory_rule": rule, "directory_template": template},
                         books, signal or threading.Event(), owner)

    def album(self, book):
        found = super().album(book)
        if found:
            return found
        album_id = self.comic_id(book["id"])
        keys = name_keys(book["title"]) | name_keys(original_name(book["title"]))
        named = []
        for path in self.children(self.root):
            rest = LEADING_GROUP.sub("", path.name, count=1)
            if rest != path.name and name_keys(rest) & keys:
                named.append(path)
        if named and any(self.titles[key] - {album_id} for key in keys):
            raise ComicApiError("书库中存在同名漫画，本地目录缺少 ID，未自动匹配", 409, "local_ambiguous", self.source)
        return self.unique(named, "漫画标题")

    def pages(self, directory, urls):
        """Page number -> file, from 1..N numbering or the source file names, whichever explains more files."""
        files = self.image_files(directory)
        expected = {Path(unquote(urlparse(url).path)).stem: page for page, url in enumerate(urls, 1)}
        numeric, named = defaultdict(list), defaultdict(list)
        for path in files:
            if re.fullmatch(r"[0-9]{1,20}", path.stem) and 1 <= int(path.stem) <= len(urls):
                numeric[int(path.stem)].append(path)
            if path.stem in expected:
                named[expected[path.stem]].append(path)
        slots = named if len(named) > len(numeric) else numeric
        # Format changes can leave one page in several formats; the newest file is the current one.
        return {page: max(paths, key=lambda path: path.stat().st_mtime_ns) for page, paths in slots.items()}


class LibraryMigration:
    def __init__(self, store, service, library):
        self.store, self.service, self.library = store, service, library
        self.task = None
        self.signal = threading.Event()
        self.preview_gate = asyncio.Event()
        self.preview_gate.set()
        self.paths = DownloadPaths(store, ROOTS_ENV, DEFAULT_ROOT)
        for env, default in LEGACY_ROOTS:
            configured = [Path(part.strip()).expanduser().resolve() for part in os.environ.get(env, "").split(os.pathsep) if part.strip()]
            for root in configured or [(store.root / default).resolve()]:
                if root.is_dir() and not any(root.is_relative_to(known) for known in self.paths.roots):
                    self.paths.roots.append(root)
        # Album moves of the latest preview, keyed by (source, comic ID); kept in memory only.
        self.plan = {}
        self.state = store.settings(STATE_KEY) or {"status": "idle"}
        if self.state.get("status") in ("previewing", "pausing", "paused", "running"):
            self.state.update(status="interrupted", error="服务重启中断；已迁移的漫画保留，需重新预览")
            self._persist()

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def _persist(self):
        self.store.save_settings(STATE_KEY, self.state)

    def status(self):
        config = self.library.settings()
        custom = config["directory_rule"] == "custom"
        return {**self.state, "active": self.active, "roots": [str(root) for root in self.paths.roots],
                "target": {"download_dir": config["download_dir"], "directory_rule": config["directory_rule"],
                           "directory_template": config["directory_template"] if custom else "",
                           "label": config["directory_template"] if custom else DIRECTORY_RULES[config["directory_rule"]]}}

    def browse(self, path=""):
        return self.paths.browse(path or self.state.get("source_dir") or "")

    def _guard(self):
        if self.active:
            raise ComicApiError("迁移任务正在运行", 409, "migration_active", "library")
        if self.library.active:
            raise ComicApiError("书库同步进行中，停止后才能迁移", 409, "sync_active", "library")

    def preview(self, source_dir):
        self._guard()
        source = self.paths.resolve(source_dir)
        if not source.is_dir():
            raise ComicApiError("迁移源目录不存在或不是目录", 422, "directory_unavailable", "library")
        self.plan = {}
        self.signal = threading.Event()
        self.preview_gate = asyncio.Event()
        self.preview_gate.set()
        self.state = {"status": "previewing", "source_dir": str(source), "scope": self._scope(source),
                      "started_at": time.time(), "finished_at": None, "albums_total": 0, "albums_done": 0,
                      "albums": [], "leftovers": [], "error": ""}
        self._persist()
        self.task = asyncio.create_task(self._preview(source))
        return self.status()

    def pause(self):
        if not self.active or self.state.get("status") != "previewing":
            raise ComicApiError("没有正在生成的迁移预览", 409, "invalid_state", "library")
        self.preview_gate.clear()
        self.state["status"] = "pausing"
        self._persist()
        return self.status()

    def resume(self):
        if not self.active or self.state.get("status") != "paused":
            raise ComicApiError("没有已暂停的迁移预览", 409, "invalid_state", "library")
        self.state["status"] = "previewing"
        self.preview_gate.set()
        self._persist()
        return self.status()

    async def _checkpoint(self):
        if self.signal.is_set():
            raise DownloadCancelled()
        while not self.preview_gate.is_set():
            self.state["status"] = "paused"
            self._persist()
            await self.preview_gate.wait()
        if self.signal.is_set():
            raise DownloadCancelled()

    async def stop(self):
        self.signal.set()
        self.preview_gate.set()
        if self.task:
            await self.task

    def run(self):
        self._guard()
        if self.state.get("status") != "ready" or not self.plan:
            raise ComicApiError("需先生成迁移预览", 409, "preview_required", "library")
        if self.state.get("scope") != self._scope(Path(self.state["source_dir"])):
            raise ComicApiError("书库同步设置已变化，需重新预览", 409, "preview_stale", "library")
        self.state.update(status="running", started_at=time.time(), finished_at=None, albums_done=0, error="")
        self._persist()
        self.library.blocked_by = "目录正在迁移，完成后才能同步书库"
        self.task = asyncio.create_task(self._run())
        return self.status()

    def _scope(self, source):
        config = self.library.settings()
        return [str(source), config["download_dir"], config["directory_rule"], config["directory_template"]]

    def _bases(self, source):
        """Where each source's comics are searched: ``<dir>/<source>`` when the directory is split per source."""
        workers = self.library._workers()
        split = [worker for worker in workers if (source / worker.source).is_dir()]
        return [(worker, source / worker.source) for worker in split] if split else [(worker, source) for worker in workers]

    async def _preview(self, source):
        state = self.state
        known = set()
        try:
            await self._checkpoint()
            bases = await asyncio.to_thread(self._bases, source)
            await self._checkpoint()
            books = defaultdict(list)
            all_books = await asyncio.to_thread(self.store.books)
            await self._checkpoint()
            for book in all_books:
                books[book["source"]].append(book)
            state["albums_total"] = sum(len(books[worker.source]) for worker, _ in bases)
            template = self.library.settings()["directory_template"]
            for worker, base in bases:
                tree = await asyncio.to_thread(SourceTree, self.store, self.paths, base, books[worker.source], worker, template, self.signal)
                for meta in books[worker.source]:
                    await self._checkpoint()
                    await self._preview_album(worker, tree, meta, known)
                    await self._checkpoint()
                    state["albums_done"] += 1
            await self._checkpoint()
            state["status"] = "ready" if self.plan else "completed"
        except DownloadCancelled:
            state.update(status="interrupted", error="服务停止中断；已迁移的漫画保留，需重新预览")
        except Exception as exc:
            state.update(status="failed", error=str(exc) if isinstance(exc, ComicApiError) else "迁移预览失败")
            log.warning("Library migration preview failed", exc_info=not isinstance(exc, ComicApiError))
        finally:
            state["finished_at"] = time.time()
            state["leftovers"] = await asyncio.to_thread(self._leftovers, source, known)
            self._persist()

    async def _preview_album(self, worker, tree, meta, known):
        try:
            album_id = worker.normalize_id(str(meta["id"]))
            recorded = (await asyncio.to_thread(worker.local_status, [album_id]))["items"].get(album_id, {})
            await self._checkpoint()
            directory = await asyncio.to_thread(tree.album, meta) or await asyncio.to_thread(self._recorded_directory, tree, recorded)
            await self._checkpoint()
        except ComicApiError as exc:
            if exc.code != "local_ambiguous":
                return
            self.state["albums"].append({"source": worker.source, "id": str(meta["id"]), "title": meta.get("title") or str(meta["id"]),
                                         "from": "", "to": "", "images": 0, "status": "failed", "message": str(exc)})
            return
        if not directory:
            return
        known.add(directory)
        entry = {"source": worker.source, "id": album_id, "title": meta.get("title") or album_id,
                 "from": str(directory), "to": "", "images": 0, "status": "failed", "message": ""}
        try:
            # A complete copy recorded at this very directory still moves when the rule now names it differently.
            in_place = recorded.get("path") == str(directory)
            if recorded.get("status") == "downloaded" and in_place and directory == worker.paths.album(worker.settings(), meta):
                entry.update(status="skipped", message="已按当前目录规则登记为本地已下载，无需迁移")
            elif recorded.get("status") == "downloaded" and not in_place:
                entry.update(status="skipped", message="书库已有完整副本，源目录文件保留原处")
                if recorded.get("path"):
                    known.add(Path(recorded["path"]))
            else:
                plan = await self._plan(worker, tree, album_id, directory)
                known.add(Path(plan["album"]))
                entry.update(title=plan["book"]["title"], to=plan["album"], images=len(plan["moves"]), status="ready",
                             message=plan["note"])
                self.plan[(worker.source, album_id)] = plan
        except DownloadCancelled:
            raise
        except Exception as exc:
            entry["message"] = str(exc) if isinstance(exc, ComicApiError) else "预览失败"
            if not isinstance(exc, ComicApiError):
                log.warning("Library migration preview failed for %s/%s", worker.source, album_id, exc_info=True)
        self.state["albums"].append(entry)

    @staticmethod
    def _recorded_directory(tree, recorded):
        """The comic directory a check or sync recorded inside the scanned tree, whatever rule named it."""
        path = Path(recorded["path"]) if recorded.get("path") else None
        return path if path and path != tree.root and path.is_relative_to(tree.root) and path.is_dir() else None

    async def _plan(self, worker, tree, album_id, directory):
        source = worker.source
        book = await self.service.get_comic_detail(source, album_id)
        await self._checkpoint()
        chapters = book.get("chapters", [])
        if not chapters:
            raise ComicApiError("漫画暂无有效章节", 502, "invalid_response", source)
        config = worker.settings()
        moves, missing_chapters, missing_pages, broken = [], 0, 0, 0
        for index, chapter in enumerate(chapters, 1):
            await self._checkpoint()
            chapter_id = worker.chapter_id(chapter["id"])
            found = await asyncio.to_thread(tree.find_chapter, directory, chapter, index)
            if not found and len(chapters) == 1 and await asyncio.to_thread(tree.image_files, directory):
                found = directory
            await self._checkpoint()
            if not found:
                missing_chapters += 1
                continue
            urls = await self.service.get_chapter_images(source, album_id, chapter_id)
            await self._checkpoint()
            if not urls:
                raise ComicApiError("章节未返回图片", 502, "invalid_response", source)
            pages = await asyncio.to_thread(tree.pages, found, urls)
            await self._checkpoint()
            target_dir = worker.paths.chapter(config, book, chapter, index)
            for page, url in enumerate(urls, 1):
                await self._checkpoint()
                path = pages.get(page)
                if path is None:
                    missing_pages += 1
                    continue
                try:
                    record = await asyncio.to_thread(tree.inspect, path)
                except ComicApiError:
                    broken += 1
                    continue
                finally:
                    await self._checkpoint()
                target = worker.paths.resolve(target_dir / f"{page:05d}{path.suffix.lower()}")
                if target.exists() and not os.path.samefile(target, path):
                    raise ComicApiError(f"目标位置已有其他文件：{target}", 409, "file_conflict", source)
                moves.append(dict(record, chapter_id=chapter_id, page=page, image_key=urlparse(url).path, target=str(target)))
        if not moves:
            raise ComicApiError("源目录中没有可与章节页码对应的图片", 409, "local_incomplete", source)
        if id_free(config):
            worker._guard_plain_directory(album_id, worker.paths.album(config, book))
        notes = []
        if missing_chapters or missing_pages:
            notes.append(f"缺少 {missing_chapters} 章、{missing_pages} 页，迁移后由书库同步补齐" if missing_chapters
                         else f"缺少 {missing_pages} 页，迁移后由书库同步补齐")
        if broken:
            notes.append(f"{broken} 张图片无法解码，保留原处")
        return {"book": book, "config": config, "base": str(tree.root), "moves": moves, "album": str(worker.paths.album(config, book)),
                "complete": not (missing_chapters or missing_pages or broken), "note": "；".join(notes)}

    async def _run(self):
        state = self.state
        entries = {(entry["source"], entry["id"]): entry for entry in state["albums"]}
        known = {Path(entry["to"] or entry["from"]) for entry in state["albums"] if entry["to"] or entry["from"]}
        try:
            for key, plan in list(self.plan.items()):
                entry = entries[key]
                try:
                    await asyncio.to_thread(self._migrate, self.library.worker(key[0]), key[1], plan)
                    entry["status"] = "migrated"
                except Exception as exc:
                    entry.update(status="failed", message=str(exc) if isinstance(exc, ComicApiError) else "迁移失败，源文件保持原样")
                    known.add(Path(entry["from"]))
                    if not isinstance(exc, ComicApiError):
                        log.warning("Library migration failed for %s/%s", *key, exc_info=True)
                self.plan.pop(key)
                state["albums_done"] += 1
                self._persist()
            state["status"] = "completed"
        except Exception:
            state.update(status="failed", error="迁移失败")
            log.warning("Library migration failed", exc_info=True)
        finally:
            self.library.blocked_by = None
            state["finished_at"] = time.time()
            state["leftovers"] = await asyncio.to_thread(self._leftovers, Path(state["source_dir"]), known)
            self._persist()

    def _migrate(self, worker, album_id, plan):
        config, book, moves = plan["config"], plan["book"], plan["moves"]
        root = worker.paths.resolve(config["download_dir"])
        for item in moves:
            path, target = Path(item["path"]), Path(item["target"])
            try:
                stat = path.stat()
            except OSError:
                stat = None
            if stat is None or (stat.st_ino, stat.st_size, stat.st_mtime_ns) != tuple(item["stamp"]):
                raise ComicApiError("源图片在预览后发生变化，需重新预览", 409, "local_changed", worker.source)
            if target.exists() and not os.path.samefile(target, path):
                raise ComicApiError(f"目标位置已有其他文件：{target}", 409, "file_conflict", worker.source)
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
                    item["size"], item["digest"]) for item in moves]
        with self.store.connect() as db:
            # Moved files leave their old checkpoints behind; at the target they are checked local copies.
            for table in (worker.files_table, worker.local_table):
                db.executemany(f"DELETE FROM {table} WHERE path=?", [(item["path"],) for item in moves])
            db.executemany(f"INSERT OR REPLACE INTO {worker.local_table} VALUES (?,?,?,?,?,?,?,?)", records)
        album_dir = plan["album"]
        if plan["complete"]:
            worker.save_local_status(root, album_id, "downloaded", book["title"], album_dir, len(records))
            worker.remember_complete(album_id, config, book, [{"path": item["target"]} for item in moves])
        else:
            worker.save_local_status(root, album_id, "error", book["title"], album_dir, message=f"迁移后{plan['note']}")
        self._prune(Path(plan["base"]), {path.parent for path, _ in moved})

    @staticmethod
    def _prune(root, directories):
        """Remove directories the move left empty, up to the scanned source directory."""
        for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            while directory != root and directory.is_relative_to(root):
                try:
                    directory.rmdir()
                except OSError:
                    break
                directory = directory.parent

    def _leftovers(self, source, known):
        """Entries of the scanned directories that hold no matched or target comic directory."""
        result = []
        try:
            bases = [base for _, base in self._bases(source)]
        except Exception:
            bases = [source]
        for root in dict.fromkeys(bases):
            try:
                names = sorted(path.name for path in root.iterdir() if not path.name.startswith(".")
                               and not any(item.is_relative_to(path) for item in known))
            except OSError:
                continue
            if names:
                result.append({"root": str(root), "entries": names[:50], "more": max(0, len(names) - 50)})
        return result
