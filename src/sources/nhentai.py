"""nhentai adapter using the official REST API v2 (https://nhentai.net/api/v2/docs).

- Public endpoints work anonymously; a user API key raises rate limits and is required for
  the official archive download.
- Image hosts come from /api/v2/cdn at runtime; the API docs forbid hard-coding CDN
  subdomains or guessing file paths.
- Whole-gallery downloads use POST /galleries/{id}/download instead of walking CDN page URLs,
  as the API docs require. The PDF pipeline calls download_archive() for this source.
"""
import os
import random
import re
import threading
import time
import urllib.parse
import zipfile

from src.clients.base import BaseClient
from src.errors import ComicApiError, DownloadCancelled
from src.metadata import comic_page_count
from src.sources import SourcePlugin
from src.sources._nhentai_tags import JM_TAG_QUERIES

API_BASE = "https://nhentai.net/api/v2"
USER_AGENT = "comic-api/1.0 (+https://github.com/Sor85/comic-api)"
TAG_TYPES = ("tag", "artist", "group", "parody", "character", "language", "category")
TAG_VALUE = re.compile(r'(%s):"([^"\x00-\x1f]{1,100})"' % "|".join(TAG_TYPES))
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif")
CACHE_SECONDS = 600
CDN_SECONDS = 3600
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_IMAGES = 3000
MAX_ARCHIVE_IMAGE_BYTES = 64 * 1024 * 1024
REDIRECTS = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 5


def _natural_key(name):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


class NhentaiPlugin(SourcePlugin):
    id = "nhentai"
    name = "nhentai"
    capabilities = ["search", "tag_search", "multi_tag_search", "author_search", "detail", "pages", "latest", "category", "leaderboard", "random", "login"]
    login_fields = [{"name": "api_key", "label": "API Key", "type": "password"}]
    login_hint = ("API Key 在 nhentai 网站「账号设置 → API Keys」生成（nhentai.net/user/settings#apikeys）。"
                  "未绑定时可搜索、浏览和阅读；绑定后请求限额更高，并可通过官方归档接口下载 PDF。")
    categories = [{"value": v, "label": n} for v, n in
                  [("doujinshi", "同人志"), ("manga", "漫画"), ("artistcg", "画师 CG"), ("gamecg", "游戏 CG"),
                   ("western", "欧美"), ("non-h", "非 H"), ("imageset", "图集"), ("cosplay", "Cosplay"),
                   ("asianporn", "亚洲写真"), ("misc", "其他")]]
    search_sorts = [{"value": v, "label": n} for v, n in
                    [("date", "最新上传"), ("popular", "总人气"), ("popular-today", "今日人气"),
                     ("popular-week", "本周人气"), ("popular-month", "本月人气")]]
    tag_sorts = search_sorts
    sorts = search_sorts
    leaderboard_modes = [{"value": v, "label": n} for v, n in
                         [("day", "今日"), ("week", "本周"), ("month", "本月"), ("total", "总榜")]]
    _LEADERBOARD = {"day": "popular-today", "week": "popular-week", "month": "popular-month", "total": "popular"}

    def __init__(self, store):
        super().__init__(store)
        self.client = BaseClient()
        # Image requests reuse client.ua; the API docs ask for a descriptive client name.
        self.client.ua = USER_AGENT
        self._lock = threading.Lock()
        self._cdn = None
        self._details = {}
        self._latest_pages = 0

    # ---------- account ----------
    def _api_key(self):
        return str((self.store.account(self.id) or {}).get("api_key") or "")

    def account_status(self):
        configured = bool(self._api_key())
        return {"authenticated": configured, "configured": configured}

    def login(self, values):
        key = str(values.get("api_key", "")).strip()
        if not key or len(key) > 256 or any(ord(char) <= 32 or ord(char) == 127 for char in key):
            raise ComicApiError("API Key 格式无效", 422, "invalid_request", self.id)
        try:
            self._api("GET", "user", key=key)
        except ComicApiError as exc:
            if exc.status_code in (401, 403):
                raise ComicApiError("API Key 无效或已撤销", 401, "login_failed", self.id) from exc
            raise
        self.store.save_account(self.id, {"api_key": key})

    def logout(self):
        self.store.save_account(self.id, {})

    # ---------- HTTP ----------
    def _api(self, method, path, key=None, **params):
        key = self._api_key() if key is None else key
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if key:
            headers["Authorization"] = f"Key {key}"
        response = self.client.request(method, f"{API_BASE}/{path}", params=params or None, headers=headers)
        status = response.status_code
        if status >= 400:
            try:
                reason = str(response.json().get("error") or "")[:200]
            except Exception:
                reason = ""
            if status == 429:
                raise ComicApiError("nhentai 请求过于频繁，请约 1 分钟后重试", 429, "rate_limited", self.id)
            if status == 404:
                raise ComicApiError("作品不存在", 404, "not_found", self.id)
            if status in (401, 403):
                message = "nhentai API Key 无效或已撤销" if key else "nhentai 此功能需要 API Key"
                raise ComicApiError(message, 401, "login_required", self.id)
            if status == 503:
                raise ComicApiError(f"nhentai 暂时关闭了此功能{('：' + reason) if reason else ''}", 503, "upstream_unavailable", self.id)
            raise ComicApiError(f"nhentai 返回 HTTP {status}{('：' + reason) if reason else ''}", 502, "upstream_error", self.id)
        try:
            return response.json()
        except ValueError as exc:
            raise ComicApiError("nhentai 响应格式已变化", 502, "invalid_response", self.id) from exc

    def _servers(self):
        with self._lock:
            if self._cdn and time.monotonic() < self._cdn[2]:
                return self._cdn[0], self._cdn[1]
        data = self._api("GET", "cdn")
        images = [s.rstrip("/") for s in data.get("image_servers") or [] if isinstance(s, str) and s.startswith("https://")]
        thumbs = [s.rstrip("/") for s in data.get("thumb_servers") or [] if isinstance(s, str) and s.startswith("https://")]
        if not images or not thumbs:
            raise ComicApiError("nhentai 图片服务器列表无效", 502, "invalid_response", self.id)
        with self._lock:
            self._cdn = (images, thumbs, time.monotonic() + CDN_SECONDS)
        return images, thumbs

    @staticmethod
    def _media_url(servers, path, index=0):
        path = str(path or "").lstrip("/")
        return f"{servers[index % len(servers)]}/{path}" if path else ""

    # ---------- mapping ----------
    def _list_item(self, item, thumbs):
        title = item.get("english_title") or item.get("japanese_title") or ""
        return {"id": str(item["id"]), "source": self.id, "title": title,
                "cover": self._media_url(thumbs, item.get("thumbnail"), int(item["id"])),
                "author": "", "authors": [], "page_count": comic_page_count(item.get("num_pages"))}

    def _items(self, data):
        rows = data.get("result") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ComicApiError("nhentai 列表响应格式无效", 502, "invalid_response", self.id)
        _, thumbs = self._servers()
        return [self._list_item(row, thumbs) for row in rows if isinstance(row, dict) and row.get("id")]

    def _gallery(self, comic_id):
        if not re.fullmatch(r"[1-9][0-9]{0,9}", str(comic_id)):
            raise ComicApiError("作品不存在", 404, "not_found", self.id)
        with self._lock:
            cached = self._details.get(comic_id)
            if cached and time.monotonic() < cached[0]:
                return cached[1]
        data = self._api("GET", f"galleries/{comic_id}")
        if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
            raise ComicApiError("nhentai 详情响应格式无效", 502, "invalid_response", self.id)
        with self._lock:
            if len(self._details) >= 128:
                self._details.pop(next(iter(self._details)))
            self._details[comic_id] = (time.monotonic() + CACHE_SECONDS, data)
        return data

    # ---------- plugin API ----------
    def search(self, keyword, page=1, sort=""):
        keyword = str(keyword or "").strip()
        if not keyword:
            return []
        return self._items(self._api("GET", "search", query=keyword, sort=sort or "date", page=page))

    @staticmethod
    def _tag_query(value):
        value = str(value or "").strip()
        if TAG_VALUE.fullmatch(value):
            return value
        name = value.replace('"', "")
        return JM_TAG_QUERIES.get(name.casefold(), f'tag:"{name}"') if name else ""

    def search_tag(self, keyword, page=1, sort="", tag_value="", tags=(), exclude_tags=()):
        if tags:
            # Space-separated terms must all match; a leading "-" excludes the term.
            include = [query for query in map(self._tag_query, tags) if query]
            query = " ".join(include + ["-" + query for query in map(self._tag_query, exclude_tags) if query])
        else:
            # Tag links carry type:"name"; typed input may also use that syntax.
            query = next((value.strip() for value in (tag_value, keyword)
                          if TAG_VALUE.fullmatch(str(value or "").strip())), "") or self._tag_query(keyword)
        if not query:
            return []
        return self._items(self._api("GET", "search", query=query, sort=sort or "date", page=page))

    def search_author(self, keyword, page=1, sort=""):
        name = str(keyword or "").strip().replace('"', "")
        if not name:
            return []
        return self._items(self._api("GET", "search", query=f'artist:"{name}"', sort=sort or "date", page=page))

    def detail(self, comic_id):
        data = self._gallery(comic_id)
        _, thumbs = self._servers()
        title = data.get("title") or {}
        name = title.get("english") or title.get("japanese") or title.get("pretty") or ""
        tags, categories, authors = [], [], []
        for tag in data.get("tags") or []:
            kind, label = tag.get("type"), tag.get("name")
            if kind not in TAG_TYPES or not isinstance(label, str) or not label.strip() or '"' in label:
                continue
            entry = {"label": label, "value": f'{kind}:"{label}"'}
            if kind == "category":
                categories.append(entry)
            else:
                tags.append(entry)
            if kind == "artist":
                authors.append(label)
        page_count = comic_page_count(data.get("num_pages")) or len(data["pages"])
        japanese = title.get("japanese") or ""
        return {"id": str(data.get("id", comic_id)), "source": self.id, "title": name,
                "cover": self._media_url(thumbs, (data.get("cover") or {}).get("path"), int(comic_id)),
                "description": japanese if japanese and japanese != name else "",
                "author": " / ".join(authors), "authors": authors, "page_count": page_count,
                "tags": tags, "categories": categories,
                "chapters": [{"id": str(comic_id), "name": f"全本（{page_count} 页）", "order": 1}]}

    def pages(self, comic_id, chapter_id):
        data = self._gallery(comic_id)
        images, _ = self._servers()
        pages = sorted((p for p in data["pages"] if isinstance(p, dict) and p.get("path")),
                       key=lambda p: int(p.get("number") or 0))
        # Deterministic host per page keeps the image proxy's cache keys stable.
        return [self._media_url(images, page["path"], index) for index, page in enumerate(pages)]

    def browse(self, action, page=1, sort="", mode="day", name=""):
        if action == "latest":
            data = self._api("GET", "galleries", page=page)
            self._latest_pages = int(data.get("num_pages") or 0)
            return self._items(data)
        if action == "random":
            # One random page of the newest list; /galleries/random returns a single ID per call.
            total = self._latest_pages or int(self._api("GET", "galleries", page=1).get("num_pages") or 1)
            self._latest_pages = total
            return self._items(self._api("GET", "galleries", page=random.randint(1, max(1, total))))
        if action == "leaderboard":
            order = self._LEADERBOARD.get(mode, "popular-today")
            return self._items(self._api("GET", "search", query="pages:>0", sort=order, page=page))
        slug = name if name in {c["value"] for c in self.categories} else ""
        query = f'category:"{slug}"' if slug else "pages:>0"
        return self._items(self._api("GET", "search", query=query, sort=sort or "date", page=page))

    # ---------- official archive download ----------
    def check_download(self):
        """Reject queueing before the task starts when the archive endpoint cannot be used."""
        if not self._api_key():
            raise ComicApiError("nhentai 下载 PDF 需要 API Key，请在「设置 → 图源管理 → nhentai → 登录凭证」绑定",
                                401, "login_required", self.id)

    def download_archive(self, comic_id, chapter_id, out_dir, progress=None, cancelled=None):
        """Fetch the official zip archive and extract its images; returns ordered file paths."""
        self.check_download()
        total = comic_page_count(self._gallery(comic_id).get("num_pages")) or 0
        if progress:
            progress("downloading", 0, total)
        data = self._api("POST", f"galleries/{comic_id}/download", format="zip")
        url = data.get("url") if isinstance(data, dict) else None
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ComicApiError("nhentai 未返回有效的下载地址", 502, "invalid_response", self.id)
        archive = os.path.join(out_dir, "archive.zip")
        self._fetch_archive(url, archive, cancelled)
        paths = self._extract_archive(archive, out_dir, cancelled)
        os.remove(archive)
        if progress:
            progress("downloading", len(paths), len(paths))
        return paths

    def _fetch_archive(self, url, path, cancelled=None):
        from src.services.image_network import image_target
        visited = set()
        for _ in range(MAX_REDIRECTS + 1):
            target = image_target(url)
            url = target.url if target else url
            if url in visited:
                raise ComicApiError("下载地址重定向存在循环", 502, "download_failed", self.id)
            visited.add(url)
            # Signed short-lived URL: never forward the API key to the archive host.
            with self.client.stream("GET", url, headers={"User-Agent": USER_AGENT}, allow_redirects=False,
                                    timeout=(15, 60), _image_target=target) as response:
                if response.status_code in REDIRECTS:
                    location = response.headers.get("Location")
                    if not location:
                        raise ComicApiError("下载地址重定向缺少目标", 502, "download_failed", self.id)
                    url = urllib.parse.urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise ComicApiError(f"归档下载失败 (HTTP {response.status_code})", 502, "download_failed", self.id)
                size = 0
                with open(path, "wb") as stream:
                    for chunk in response.iter_content():
                        if cancelled and cancelled():
                            raise DownloadCancelled()
                        size += len(chunk)
                        if size > MAX_ARCHIVE_BYTES:
                            raise ComicApiError("归档超过 1 GiB 限制", 502, "download_failed", self.id)
                        stream.write(chunk)
                return
        raise ComicApiError("下载地址重定向次数过多", 502, "download_failed", self.id)

    def _extract_archive(self, archive, out_dir, cancelled=None):
        try:
            with zipfile.ZipFile(archive) as bundle:
                members = [m for m in bundle.infolist()
                           if not m.is_dir() and os.path.splitext(m.filename)[1].lower() in IMAGE_SUFFIXES]
                if not members:
                    raise ComicApiError("归档中没有图片", 502, "invalid_response", self.id)
                if len(members) > MAX_ARCHIVE_IMAGES or sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES * 2:
                    raise ComicApiError("归档内容超出限制", 502, "download_failed", self.id)
                members.sort(key=lambda m: _natural_key(os.path.basename(m.filename)))
                paths = []
                for index, member in enumerate(members):
                    if cancelled and cancelled():
                        raise DownloadCancelled()
                    if member.file_size > MAX_ARCHIVE_IMAGE_BYTES:
                        raise ComicApiError("归档中的单张图片超过 64 MiB 限制", 502, "download_failed", self.id)
                    # Archive member names are never used as paths.
                    suffix = os.path.splitext(member.filename)[1].lower()
                    target = os.path.join(out_dir, f"{index + 1:04d}{suffix}")
                    with bundle.open(member) as source, open(target, "wb") as stream:
                        remaining = MAX_ARCHIVE_IMAGE_BYTES
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            remaining -= len(chunk)
                            if remaining < 0:
                                raise ComicApiError("归档中的单张图片超过 64 MiB 限制", 502, "download_failed", self.id)
                            stream.write(chunk)
                    paths.append(target)
                return paths
        except zipfile.BadZipFile as exc:
            raise ComicApiError("归档文件损坏", 502, "invalid_response", self.id) from exc


create_plugin = NhentaiPlugin
