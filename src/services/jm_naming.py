"""JMComic 2.6.13 directory naming, with commonX 0.6.40 filename semantics.

The original-name parser is adapted from JMComic-Crawler-Python (MIT).
See THIRD_PARTY_NOTICES.md. No transliteration or simplified/traditional conversion.
"""
from pathlib import PurePath

from src.errors import ComicApiError

JM_DIRECTORY_RULE = "Bd_Aauthoroname_Pindextitle"
BRACKETS = {"(": ")", "[": "]", "【": "】", "（": "）"}
FORBIDDEN = str.maketrans({char: "_" for char in '\\/:*?"<>|\n\t\r'})
# Common Linux filesystems (ext4, ZFS, btrfs) cap a single name at 255 bytes.
MAX_NAME_BYTES = 255
# JMComic 2.6.13 JmcomicText.try_mkdir: on ENAMETOOLONG it retries the whole save path cut to this many characters.
JM_PATH_LIMIT = 100


def original_name(value):
    """First non-bracket token, not a concatenation of all text outside brackets."""
    title = str(value).strip()
    start = position = 0
    while position < len(title):
        char = title[position]
        if char not in BRACKETS:
            position += 1
            continue
        token = title[start:position].strip()
        if token and token[0] not in BRACKETS:
            return token
        stack = [char]
        end = position + 1
        while end < len(title) and stack:
            char = title[end]
            if char in BRACKETS:
                stack.append(char)
            elif char == BRACKETS[stack[-1]]:
                stack.pop()
            end += 1
        if stack:
            # An unclosed opener remains ordinary text until the next opener.
            start = position
            position += 1
        else:
            start = position = end
    token = title[start:].strip()
    return token if token and token[0] not in BRACKETS else title


def directory_name(value):
    # Order matters: commonX removes trailing periods before DirRule strips whitespace.
    return str(value).translate(FORBIDDEN).rstrip(".").strip()


def fits(name):
    return len(name.encode("utf-8")) <= MAX_NAME_BYTES


def fit_name(name):
    """An over-long name keeps its longest prefix within the filesystem limit, cleaned like any other name."""
    if fits(name):
        return name
    return directory_name(name.encode("utf-8")[:MAX_NAME_BYTES].decode("utf-8", errors="ignore"))


def album_name(book):
    authors = book.get("authors")
    if authors is None:
        author = book.get("author") or "default_author"
        if isinstance(author, list):
            author = author[0] if author else "default_author"
    else:
        author = authors[0] if authors else "default_author"
    # JMComic loses the chapter level when the album part is cut, so an over-long album name is cut alone.
    return fit_name(directory_name(f"【{author}】{original_name(book['title'])}"))


def chapter_name(chapter):
    try:
        # The chapter API's metadata replaces the album's episode stub in JMComic.
        index = int(chapter["sort"])
        if int(chapter["series_id"]) == 0 and index == 2:
            index = 1
        if chapter["name"] is None:
            raise ValueError("Missing chapter title")
        title = str(chapter["name"]).strip()
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ComicApiError("章节缺少 JM 原始标题或序号，无法按 Pindextitle 命名", 502, "invalid_response", "jm") from exc
    return directory_name(f"第{index}話 {title}")


def chapter_directory(album_dir, chapter):
    """Pindextitle directory inside album_dir. An over-long name is cut like JMComic cuts its whole save path; when
    that cut loses the 第N話 label, which keeps chapters apart, only the name is cut to the filesystem limit."""
    name = chapter_name(chapter)
    if fits(name):
        return name
    head = f"{PurePath(album_dir).as_posix()}/"
    cut = f"{head}{name}"[:JM_PATH_LIMIT][len(head):]
    return cut if cut_of(cut, name) and fits(cut) else fit_name(name)


def cut_of(name, full):
    """Whether name is a cut of the Pindextitle name full that still holds its 第N話 label."""
    return len(name) > full.index("話") and full.startswith(name)


def validate_name(name):
    if not name or name in (".", "..") or "\0" in name:
        raise ComicApiError("JM 目录名为空或无效", 422, "invalid_directory", "jm")
    return name
