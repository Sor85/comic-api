"""Conservative, page-local search grouping; every distinct source entry stays accessible."""
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from src.metadata import comic_authors, comic_page_count


_LANGUAGE_LABELS = {"中文", "中国語", "中國語", "汉化", "漢化", "汉化版", "漢化版", "中文版",
                    "中文翻译", "中文翻譯", "中国翻訳", "中國翻訳", "chinese", "english", "日本語", "日文"}
_GENERIC_TITLES = {"无题", "無題", "未命名", "untitled", "合集", "短篇", "短篇集"}
_BRACKETS = re.compile(r"\[([^\[\]]*)\]|【([^【】]*)】|\(([^()]*)\)")


def _text_key(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def _title_key(title, authors):
    if not isinstance(title, str):
        return '', ()
    title = unicodedata.normalize('NFKC', title).casefold()

    def strip_metadata(match):
        label = next(part for part in match.groups() if part is not None).strip()
        # Strip only recognizable metadata, not arbitrary subtitles, volumes or editions.
        if label in _LANGUAGE_LABELS or _text_key(label) in authors:
            return ' '
        if (label.endswith(('汉化组', '漢化組', '汉化', '漢化'))
                and not re.search(r'[\d卷巻话話章节節篇集]', label)):
            return ' '
        return match.group(0)

    title = _BRACKETS.sub(strip_metadata, title)
    # Preserve ranges/decimals: "1-2", "1.2" and "12" are different volume markers.
    numbers = tuple(re.sub(r'\s+', '', number)
                    for number in re.findall(r'\d+(?:\s*[-–—~〜/.:]\s*\d+)*', title))
    return _text_key(title), numbers


@dataclass(frozen=True)
class _Fingerprint:
    title: tuple
    authors: frozenset
    pages: int | None

    @classmethod
    def of(cls, item):
        names = comic_authors(item.get('authors')) or comic_authors(item.get('author'))
        authors = frozenset(key for name in names if (key := _text_key(name)))
        return cls(_title_key(item.get('title'), authors), authors, comic_page_count(item.get('page_count')))

    def matches(self, other):
        if self.title != other.title or not self.title[0] or self.title[0] in _GENERIC_TITLES:
            return False
        if self.authors and other.authors and self.authors != other.authors:
            return False
        if self.pages and other.pages:
            # Allow a few covers/credits only with matching authors, never large content gaps.
            tolerance = min(4, max(1, int(min(self.pages, other.pages) * .03)))
            if abs(self.pages - other.pages) > tolerance:
                return False
        if self.authors and self.authors == other.authors:
            return len(self.title[0]) >= 2
        # Missing authors require a reasonably specific title AND exactly equal known page counts.
        return len(self.title[0]) >= 6 and self.pages is not None and self.pages == other.pages


def deduplicate_results(items):
    """Keep ranking stable, collapse repeated IDs, and retain originals as flat variants.

    Groups are complete-link: missing metadata cannot bridge two conflicting books.
    Ambiguous entries that match several groups are kept separately.
    """
    groups, fingerprints = [], []
    buckets, seen = defaultdict(list), set()
    for item in items:
        source, comic_id = item.get('source'), item.get('id')
        if source and comic_id is not None and str(comic_id):
            identity = source, str(comic_id)
            if identity in seen:
                continue
            seen.add(identity)
        fingerprint = _Fingerprint.of(item)
        matches = [index for index in buckets[fingerprint.title]
                   if all(fingerprint.matches(member) for member in fingerprints[index])]
        if len(matches) == 1:
            index = matches[0]
            groups[index].append(item)
            fingerprints[index].append(fingerprint)
        else:
            buckets[fingerprint.title].append(len(groups))
            groups.append([item])
            fingerprints.append([fingerprint])
    return [dict(group[0], variants=[dict(item) for item in group]) if len(group) > 1 else dict(group[0])
            for group in groups]


def search_result_payload(items, all_results, errors, *, deduplicate=False):
    grouped = deduplicate_results(items) if deduplicate else items
    return {'items': grouped, 'all_results': all_results, 'errors': errors,
            'raw_count': len(items), 'duplicate_count': len(items) - len(grouped)}
