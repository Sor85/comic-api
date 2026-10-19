"""Read-only discovery of existing library images; imported files never become owned output."""
import re
import unicodedata
import warnings
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image

from src.errors import ComicApiError, DownloadCancelled
from src.services.local_images import MAX_IMAGE_BYTES, file_digest, plain_chapter_name, safe_name

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"}
COVER_STEMS = {"cover", "folder", "thumb", "poster"}


def name_keys(value):
    if not str(value or "").strip():
        return set()
    return {" ".join(unicodedata.normalize("NFKC", name).casefold().split()).strip(" .")
            for name in (str(value or ""), safe_name(value), safe_name(value, max_bytes=None))}


def directory_id(name, pattern):
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
    def __init__(self, store, paths, config, books, signal, owner):
        self.store, self.paths, self.signal = store, paths, signal
        self.source, self.files_table, self.local_table = owner.source, owner.files_table, owner.local_table
        self.id_pattern, self.chapter_pattern = owner.id_pattern, owner.chapter_pattern
        self.comic_id, self.chapter_id = owner.comic_id, owner.chapter_id
        self.root = paths.resolve(config["download_dir"])
        self.plain_names = config["directory_rule"] == "title_plain"
        if not self.root.is_dir():
            raise ComicApiError("本地检查目录不存在或不是目录", 422, "directory_unavailable", self.source)
        self.directories, self.inspected = {}, {}
        self.titles = defaultdict(set)
        for book in books:
            self.add_title(book)

    def add_title(self, book):
        """Same-title books cannot be told apart by directory name alone; record every title in scope."""
        for key in name_keys(book.get("title")):
            self.titles[key].add(str(book["id"]))

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
        author_keys = name_keys(book.get("author") or "未知作者")
        for directory in list(candidates):
            if name_keys(directory.name) & author_keys:
                candidates.extend(self.children(directory))
        identified = [p for p in candidates if directory_id(p.name, self.id_pattern) == album_id.lower()]
        keys = name_keys(book["title"])
        named = [p for p in candidates if (self.plain_names or directory_id(p.name, self.id_pattern) is None) and name_keys(p.name) & keys]
        if identified and not (self.plain_names and named):
            return self.unique(identified, "漫画 ID")
        if named and any(self.titles[key] - {album_id} for key in keys):
            raise ComicApiError("书库中存在同名漫画，本地目录缺少 ID，未自动匹配", 409, "local_ambiguous", self.source)
        return self.unique(named, "漫画标题")

    def chapter(self, album, chapter, index):
        found = self.find_chapter(album, chapter, index)
        if found:
            return found
        chapter_id = self.chapter_id(chapter["id"])
        raise ComicApiError(f"缺少可匹配的章节目录：{chapter.get('name') or chapter_id}", 409, "local_incomplete", self.source)

    def find_chapter(self, album, chapter, index):
        """The matched chapter directory, or None when no candidate exists; ambiguity still raises."""
        candidates = self.children(album)
        chapter_id = self.chapter_id(chapter["id"])
        identified = [p for p in candidates if directory_id(p.name, self.chapter_pattern) == chapter_id.lower()]
        keys = name_keys(chapter.get("name")) | name_keys(plain_chapter_name(chapter, index))
        named = []
        for directory in candidates:
            if not self.plain_names and directory_id(directory.name, self.chapter_pattern) is not None:
                continue
            if name_keys(directory.name) & keys:
                named.append(directory)
                continue
            match = re.match(r"^(?:第\s*([0-9]+)\s*[話话章集]|([0-9]+)\s*[-._])\s*(.+)$", directory.name)
            if match and int(match[1] or match[2]) == index and name_keys(match[3]) & keys:
                named.append(directory)
        if identified and not (self.plain_names and named):
            return self.unique(identified, "章节 ID")
        return self.unique(named, "章节标题")

    @staticmethod
    def episode_title(name):
        """The title part of a '第N話 title' directory name, or None for other names."""
        match = re.fullmatch(r"第\s*([0-9]+)\s*[話话]\s*(.*)", name, re.DOTALL)
        return match[2].strip() if match else None

    @staticmethod
    def image_files(directory):
        # Only named cover assets are excluded; extra numbered pages are a mismatch.
        return [p for p in directory.iterdir() if not p.name.startswith(".") and p.suffix.lower() in IMAGE_EXTENSIONS
                and p.stem.casefold() not in COVER_STEMS]

    def images(self, directory, urls):
        self.check()
        directory = self.paths.resolve(directory)
        files = self.image_files(directory)
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
            # A repair validates pages before re-checking the album; unchanged files are decoded once per run.
            cached = self.inspected.get(str(path))
            if cached and cached["stamp"] == (before.st_ino, before.st_size, before.st_mtime_ns):
                return dict(cached)
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
            result = {"path": str(path), "size": after.st_size, "digest": digest,
                      "stamp": (after.st_ino, after.st_size, after.st_mtime_ns)}
            self.inspected[str(path)] = result
            return dict(result)
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
