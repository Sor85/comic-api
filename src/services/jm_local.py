"""Read-only discovery of existing favorite images; imported files never become owned output."""
import re
import unicodedata
import warnings
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image

from src.errors import ComicApiError, DownloadCancelled
from src.services.jm_naming import JM_DIRECTORY_RULE, album_name, chapter_name
from src.services.local_images import MAX_IMAGE_BYTES, file_digest, numeric_id, plain_chapter_name, safe_name

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"}


def name_keys(value):
    if not str(value or "").strip():
        return set()
    return {" ".join(unicodedata.normalize("NFKC", name).casefold().split()).strip(" .")
            for name in (str(value or ""), safe_name(value), safe_name(value, max_bytes=None))}


def directory_id(name, pattern=r"[0-9]{1,20}"):
    match = re.fullmatch(f"({pattern})", name, re.IGNORECASE) or re.search(f" \\[({pattern})\\]$", name, re.IGNORECASE)
    return match[1].lower() if match else None


def local_file_valid(paths, root, row, url, signal):
    if not row or row["image_key"] != urlparse(url).path:
        return False
    try:
        path = paths.resolve(row["path"])
        return (path.is_relative_to(root) and path.is_file() and row["size"] > 0
                and path.stat().st_size == row["size"] and file_digest(path, signal) == row["digest"])
    except (OSError, ComicApiError):
        return False


class LocalLibrary:
    def __init__(self, store, paths, config, favorites, signal, owner=None):
        self.store, self.paths, self.signal = store, paths, signal
        self.source = getattr(owner, "source", "jm")
        self.files_table = getattr(owner, "files_table", "jm_sync_files")
        self.local_table = getattr(owner, "local_table", "jm_sync_local_files")
        self.id_pattern = getattr(owner, "id_pattern", r"[0-9]{1,20}")
        self.comic_id = getattr(owner, "comic_id", numeric_id)
        self.root = paths.resolve(config["download_dir"])
        self.plain_names = config["directory_rule"] == "title_plain"
        self.jm_names = config["directory_rule"] == JM_DIRECTORY_RULE
        if not self.root.is_dir():
            raise ComicApiError("本地检查目录不存在或不是目录", 422, "directory_unavailable", self.source)
        self.directories = {}
        self.books = {str(book["id"]): book for book in favorites}
        self.titles = defaultdict(set)
        for album_id, book in self.books.items():
            keys = {album_name(book)} if self.jm_names else name_keys(book.get("title"))
            for key in keys:
                self.titles[key].add(album_id)

    def check(self):
        if self.signal.is_set():
            raise DownloadCancelled()

    def children(self, directory):
        self.check()
        directory = self.paths.resolve(directory)
        if directory not in self.directories:
            try:
                self.directories[directory] = [p for p in directory.iterdir()
                                               if not p.name.startswith(".") and not p.is_symlink() and p.is_dir()]
            except OSError as exc:
                raise ComicApiError("本地漫画目录不可读取", 403, "directory_unavailable", self.source) from exc
        return self.directories[directory]

    def unique(self, candidates, label):
        if len(candidates) > 1:
            raise ComicApiError(f"{label}匹配到多个目录，未认定为已下载", 409, "local_ambiguous", self.source)
        return candidates[0] if candidates else None

    def album(self, book):
        album_id = self.comic_id(book["id"])
        candidates = list(self.children(self.root))
        if self.jm_names:
            expected = album_name(book)
            named = [path for path in candidates if path.name == expected]
            if named and self.titles[expected] - {album_id}:
                raise ComicApiError("多个收藏生成相同 Aauthoroname 目录，未自动匹配", 409, "local_ambiguous", self.source)
            return self.unique(named, "Aauthoroname")
        author_keys = name_keys(book.get("author") or "未知作者")
        for directory in list(candidates):
            if name_keys(directory.name) & author_keys:
                candidates.extend(self.children(directory))
        identified = [p for p in candidates if directory_id(p.name, self.id_pattern) == album_id]
        keys = name_keys(book["title"])
        named = [p for p in candidates if (self.plain_names or directory_id(p.name, self.id_pattern) is None) and name_keys(p.name) & keys]
        if identified and not (self.plain_names and named):
            return self.unique(identified, "漫画 ID")
        if named and any(self.titles[key] - {album_id} for key in keys):
            raise ComicApiError("收藏中存在同名漫画，本地目录缺少 ID，未自动匹配", 409, "local_ambiguous", self.source)
        return self.unique(named, "漫画标题")

    def chapter(self, album, chapter, index):
        candidates = self.children(album)
        if self.jm_names:
            expected = chapter_name(chapter)
            found = self.unique([path for path in candidates if path.name == expected], "Pindextitle")
            if not found:
                raise ComicApiError(f"缺少 Pindextitle 章节目录：{expected}", 409, "local_incomplete", self.source)
            return found
        chapter_id = numeric_id(chapter["id"], self.source)
        identified = [p for p in candidates if directory_id(p.name) == chapter_id]
        keys = name_keys(chapter.get("name")) | name_keys(plain_chapter_name(chapter, index))
        named = []
        for directory in candidates:
            if not self.plain_names and directory_id(directory.name) is not None:
                continue
            if name_keys(directory.name) & keys:
                named.append(directory)
                continue
            match = re.match(r"^(?:第\s*([0-9]+)\s*[話话章集]|([0-9]+)\s*[-._])\s*(.+)$", directory.name)
            if match and int(match[1] or match[2]) == index and name_keys(match[3]) & keys:
                named.append(directory)
        if identified and not (self.plain_names and named):
            return self.unique(identified, "章节 ID")
        found = self.unique(named, "章节标题")
        if not found:
            raise ComicApiError(f"缺少可匹配的章节目录：{chapter.get('name') or chapter_id}", 409, "local_incomplete", self.source)
        return found

    def images(self, directory, urls):
        self.check()
        directory = self.paths.resolve(directory)
        files = [p for p in directory.iterdir() if not p.name.startswith(".") and p.suffix.lower() in IMAGE_EXTENSIONS]
        # Only named cover assets are excluded; extra numbered pages are a mismatch.
        files = [p for p in files if p.stem.casefold() not in {"cover", "folder", "thumb", "poster"}]
        if len(files) != len(urls):
            raise ComicApiError(f"章节图片数量不符：本地 {len(files)} 张，远端 {len(urls)} 张", 409, "local_incomplete", self.source)
        numbers = {int(p.stem): p for p in files if re.fullmatch(r"[0-9]{1,20}", p.stem)}
        if len(numbers) == len(urls) and set(numbers) == set(range(1, len(urls) + 1)):
            return [numbers[page] for page in range(1, len(urls) + 1)]
        stems = {p.stem: p for p in files}
        expected = [Path(unquote(urlparse(url).path)).stem for url in urls]
        if len(stems) == len(urls) and len(set(expected)) == len(urls) and set(stems) == set(expected):
            return [stems[name] for name in expected]
        raise ComicApiError("图片页码不连续、重复或无法与远端文件名对应", 409, "local_incomplete", self.source)

    def inspect(self, path):
        self.check()
        path = self.paths.resolve(path)
        try:
            before = path.stat()
            if not path.is_file() or not 0 < before.st_size <= MAX_IMAGE_BYTES:
                raise ValueError("invalid file size")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    image.verify()
                with Image.open(path) as image:
                    for frame in range(getattr(image, "n_frames", 1)):
                        self.check()
                        image.seek(frame)
                        image.load()
            digest = file_digest(path, self.signal)
            after = self.paths.resolve(path).stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError("file changed during validation")
            return {"path": str(path), "size": after.st_size, "digest": digest,
                    "stamp": (after.st_ino, after.st_size, after.st_mtime_ns)}
        except (OSError, ValueError, EOFError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ComicApiError(f"图片损坏、不可读、过大或检查期间发生变化：{path.name}", 409, "local_invalid_image", self.source) from exc

    def validate_chapter(self, album_id, chapter_id, directory, urls):
        result = []
        for page, (path, url) in enumerate(zip(self.images(directory, urls), urls), 1):
            record = dict(self.inspect(path), chapter_id=chapter_id, page=page, image_key=urlparse(url).path)
            with self.store.connect() as db:
                owned = db.execute(f"SELECT * FROM {self.files_table} WHERE path=?", (record["path"],)).fetchall()
                imported = db.execute(f"SELECT * FROM {self.local_table} WHERE path=?", (record["path"],)).fetchall()
            for previous in [*owned, *imported]:
                if (previous["album_id"] != album_id or previous["chapter_id"] != chapter_id
                        or previous["page"] != page or previous["image_key"] != record["image_key"]
                        or previous["size"] != record["size"] or previous["digest"] != record["digest"]):
                    raise ComicApiError(f"图片与已有归属或校验记录不一致：{path.name}", 409, "local_conflict", self.source)
            record["owned"] = bool(owned)
            result.append(record)
        return result

    def remember(self, album_id, records):
        for row in records:
            self.check()
            try:
                stat = self.paths.resolve(row["path"]).stat()
                if (stat.st_ino, stat.st_size, stat.st_mtime_ns) != row["stamp"]:
                    raise OSError("file changed after validation")
            except OSError as exc:
                raise ComicApiError("检查期间本地图片发生变化，未登记本书", 409, "local_changed", self.source) from exc
        with self.store.connect() as db:
            db.execute(f"DELETE FROM {self.local_table} WHERE root=? AND album_id=?", (str(self.root), album_id))
            for row in records:
                self.check()
                if not row["owned"]:
                    db.execute(f"INSERT INTO {self.local_table} VALUES (?,?,?,?,?,?,?,?)",
                               (str(self.root), album_id, row["chapter_id"], row["page"], row["image_key"],
                                row["path"], row["size"], row["digest"]))
