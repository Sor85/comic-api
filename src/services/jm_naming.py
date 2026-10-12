"""JMComic 2.6.13 directory naming, with commonX 0.6.40 filename semantics.

The original-name parser is adapted from JMComic-Crawler-Python (MIT).
See THIRD_PARTY_NOTICES.md. No transliteration or simplified/traditional conversion.
"""
from src.errors import ComicApiError

JM_DIRECTORY_RULE = "Bd_Aauthoroname_Pindextitle"
BRACKETS = {"(": ")", "[": "]", "【": "】", "（": "）"}
FORBIDDEN = str.maketrans({char: "_" for char in '\\/:*?"<>|\n\t\r'})


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


def album_name(book):
    authors = book.get("authors")
    if authors is None:
        author = book.get("author") or "default_author"
        if isinstance(author, list):
            author = author[0] if author else "default_author"
    else:
        author = authors[0] if authors else "default_author"
    return directory_name(f"【{author}】{original_name(book['title'])}")


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


def validate_name(name):
    if not name or name in (".", "..") or "\0" in name or len(name.encode("utf-8")) > 255:
        raise ComicApiError("JM 目录名无效或超过 255 字节，未截断或改写原标题", 422, "invalid_directory", "jm")
    return name
