import asyncio
import io
import zipfile
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from PIL import Image

from src.errors import ComicApiError
from src.services.aggregator import AggregatorService
from src.sources import SourcePlugin
from src.sources._nhentai_tags import JM_TAG_QUERIES
from src.sources.nhentai import TAG_VALUE, NhentaiPlugin
from src.storage import Store

CDN = {"image_servers": ["https://i1.nh.test", "https://i2.nh.test"],
       "thumb_servers": ["https://t1.nh.test", "https://t2.nh.test"]}
GALLERY = {
    "id": 123, "media_id": "999",
    "title": {"english": "English Title", "japanese": "日本語タイトル", "pretty": "Pretty"},
    "cover": {"path": "galleries/999/cover.webp"}, "thumbnail": {"path": "galleries/999/thumb.webp"},
    "num_pages": 3,
    "tags": [{"type": "artist", "name": "some artist"}, {"type": "tag", "name": "big breasts"},
             {"type": "category", "name": "doujinshi"}, {"type": "language", "name": "english"},
             {"type": "tag", "name": 'bad "quote'}],
    "pages": [{"number": 2, "path": "galleries/999/2.png"}, {"number": 1, "path": "galleries/999/1.webp"},
              {"number": 3, "path": "/galleries/999/3.jpg"}],
}
LIST = {"result": [{"id": 5, "english_title": "Five", "thumbnail": "galleries/5/thumb.webp", "num_pages": 20},
                   {"id": 6, "english_title": None, "japanese_title": "六", "thumbnail": "galleries/6/thumb.jpg"}],
        "num_pages": 4, "per_page": 25, "total": 100}


class Response:
    def __init__(self, status, payload=None, headers=None, chunks=()):
        self.status_code, self.payload, self.headers, self.chunks = status, payload, headers or {}, chunks

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def iter_content(self):
        return iter(self.chunks)


@pytest.fixture
def plugin(tmp_path):
    instance = NhentaiPlugin(Store(tmp_path))
    instance.calls = []

    def request(method, url, params=None, headers=None, **kwargs):
        instance.calls.append((method, url, params, headers))
        path = url.split("/api/v2/", 1)[1]
        if path == "cdn":
            return Response(200, CDN)
        if path == "galleries/123":
            return Response(200, GALLERY)
        if path == "galleries/404":
            return Response(404, {"error": "Gallery not found"})
        if path == "user":
            return Response(200, {"id": 1}) if headers.get("Authorization") == "Key good" else Response(401, {"error": "Authentication required"})
        if path in ("search", "galleries"):
            return Response(200, LIST)
        if path == "galleries/123/download":
            return Response(200, {"url": "https://dl.nh.test/a.zip?sig=1", "expires_at": 1})
        return Response(500, ValueError())

    instance.client = SimpleNamespace(ua="test", request=request)
    return instance


def test_detail_maps_tags_authors_and_single_chapter(plugin):
    detail = plugin.detail("123")
    assert detail["title"] == "English Title"
    assert detail["description"] == "日本語タイトル"
    assert detail["authors"] == ["some artist"] and detail["author"] == "some artist"
    assert detail["page_count"] == 3
    assert detail["cover"].endswith("/galleries/999/cover.webp")
    assert detail["cover"].startswith(("https://t1.nh.test/", "https://t2.nh.test/"))
    assert {"label": "big breasts", "value": 'tag:"big breasts"'} in detail["tags"]
    assert {"label": "english", "value": 'language:"english"'} in detail["tags"]
    assert detail["categories"] == [{"label": "doujinshi", "value": 'category:"doujinshi"'}]
    assert all('quote' not in tag["label"] for tag in detail["tags"])
    assert detail["chapters"] == [{"id": "123", "name": "全本（3 页）", "order": 1}]


def test_pages_use_cdn_servers_in_page_order_and_cache(plugin):
    urls = plugin.pages("123", "123")
    assert [url.rsplit("/", 1)[1] for url in urls] == ["1.webp", "2.png", "3.jpg"]
    assert all(url.startswith(tuple(CDN["image_servers"])) for url in urls)
    assert "//galleries" not in "".join(url.split("://", 1)[1] for url in urls)
    count = len(plugin.calls)
    assert plugin.pages("123", "123") == urls
    assert len(plugin.calls) == count


def test_search_and_list_mapping(plugin):
    items = plugin.search("five", 2, sort="popular")
    assert [item["id"] for item in items] == ["5", "6"]
    assert items[1]["title"] == "六" and items[0]["page_count"] == 20 and items[1]["page_count"] is None
    method, url, params, headers = [call for call in plugin.calls if call[1].endswith("/search")][0]
    assert params == {"query": "five", "sort": "popular", "page": 2}
    assert "Authorization" not in headers and headers["User-Agent"].startswith("comic-api/")
    assert plugin.search("  ") == []


def test_author_search_uses_artist_scope_not_title_or_jm_tag_mapping(plugin):
    plugin.search_author('some artist', page=3, sort='popular')
    plugin.search_author('女性向')
    plugin.search_author('weird" OR name')
    count = len(plugin.calls)
    assert plugin.search_author('  ') == plugin.search_author('""') == []
    assert len(plugin.calls) == count
    queries = [call[2] for call in plugin.calls if call[1].endswith('/search')]
    assert queries == [
        {'query': 'artist:"some artist"', 'sort': 'popular', 'page': 3},
        {'query': 'artist:"女性向"', 'sort': 'date', 'page': 1},
        {'query': 'artist:"weird OR name"', 'sort': 'date', 'page': 1}]
    assert 'author_search' in plugin.manifest()['capabilities']


def test_author_api_dispatches_to_nhentai_artist_search(plugin, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv('DATA_DIR', str(plugin.store.root))
    from main import create_app

    with TestClient(create_app(plugin.store, {'nhentai': plugin})) as client:
        for scope in ({'source': 'nhentai', 'sort': 'popular'}, {}):
            response = client.get('/api/search', params={
                'keyword': 'some artist', 'mode': 'author', 'page': 2, **scope})
            assert response.status_code == 200
            assert [item['id'] for item in response.json()['items']] == ['5', '6']
    searches = [call[2] for call in plugin.calls if call[1].endswith('/search')]
    assert searches == [
        {'query': 'artist:"some artist"', 'sort': 'popular', 'page': 2},
        {'query': 'artist:"some artist"', 'sort': 'date', 'page': 2}]


def test_tag_search_accepts_typed_values_and_plain_names(plugin):
    plugin.search_tag("big breasts", tag_value='tag:"big breasts"')
    plugin.search_tag("plain name")
    plugin.search_tag('artist:"some artist"')
    plugin.search_tag('weird" OR name')
    queries = [call[2]["query"] for call in plugin.calls if call[1].endswith("/search")]
    assert queries == ['tag:"big breasts"', 'tag:"plain name"', 'artist:"some artist"', 'tag:"weird OR name"']


def test_multi_tag_search_builds_native_include_and_exclude_query(plugin):
    plugin.search_tag("ignored", page=2, sort="popular", tags=["巨乳", 'artist:"some artist"', 'plain "name'],
                      exclude_tags=["中文", 'language:"chinese"', '""'])
    searches = [call[2] for call in plugin.calls if call[1].endswith("/search")]
    assert searches == [{"query": 'tag:"big breasts" artist:"some artist" tag:"plain name" '
                                  '-tag:"中文" -language:"chinese"', "sort": "popular", "page": 2}]


@pytest.mark.parametrize("keyword,query", list(JM_TAG_QUERIES.items()))
def test_tag_search_maps_verified_jm_aliases_without_extra_requests(plugin, keyword, query):
    plugin.search_tag(f" {keyword} ", page=3, sort="popular")
    searches = [call for call in plugin.calls if call[1].endswith("/search")]
    assert len(searches) == 1
    assert searches[0][2] == {"query": query, "sort": "popular", "page": 3}
    assert TAG_VALUE.fullmatch(query)
    assert all(call[1].endswith(("/search", "/cdn")) for call in plugin.calls)


@pytest.mark.parametrize("keyword,query", [
    ("女性向", 'tag:"josei"'), ("全彩", 'tag:"full color"'),
    ("女僕", 'tag:"maid"'), ("女仆", 'tag:"maid"'),
    ("眼鏡", 'tag:"glasses"'), ("眼镜", 'tag:"glasses"'),
    ("NTR", 'tag:"netorare"'), ("nTr", 'tag:"netorare"'), ("YAOI", 'tag:"yaoi"'),
])
def test_tag_search_recognizes_jm_script_variants_and_ascii_case(plugin, keyword, query):
    plugin.search_tag(keyword)
    assert plugin.calls[0][2]["query"] == query


@pytest.mark.parametrize("keyword", ["JM独有标签", "純愛", "纯爱", "催眠", "女性向作品", "josei", "full color"])
def test_tag_search_does_not_guess_unmapped_or_partial_aliases(plugin, keyword):
    plugin.search_tag(keyword)
    assert plugin.calls[0][2]["query"] == f'tag:"{keyword}"'


def test_tag_aliases_do_not_override_native_queries_or_keyword_search(plugin):
    plugin.search_tag("女性向", tag_value='artist:"some artist"')
    plugin.search_tag('tag:"女性向"')
    plugin.search_tag('language:"chinese"')
    plugin.search("女性向")
    queries = [call[2]["query"] for call in plugin.calls if call[1].endswith("/search")]
    assert queries == ['artist:"some artist"', 'tag:"女性向"', 'language:"chinese"', "女性向"]


def test_jm_alias_mapping_applies_to_single_source_and_aggregate_api(plugin, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATA_DIR", str(plugin.store.root))
    from main import create_app

    class JmSearchPlugin(SourcePlugin):
        id = "jm"
        capabilities = ["search", "tag_search"]

        def search_tag(self, keyword, page=1):
            assert keyword == "女性向" and page == 2
            return [{"id": "1", "title": "JM result"}]

    with TestClient(create_app(plugin.store, {"jm": JmSearchPlugin(plugin.store), "nhentai": plugin})) as client:
        single = client.get("/api/search", params={"keyword": "女性向", "mode": "tag", "source": "nhentai", "page": 2})
        assert single.status_code == 200
        assert [item["id"] for item in single.json()["items"]] == ["5", "6"]
        aggregate = client.get("/api/search", params={"keyword": "女性向", "mode": "tag", "page": 2})
        assert aggregate.status_code == 200
        assert set(aggregate.json()["all_results"]) == {"jm", "nhentai"}
        assert aggregate.json()["errors"] == {}
    searches = [call for call in plugin.calls if call[1].endswith("/search")]
    assert [call[2] for call in searches] == [{"query": 'tag:"josei"', "sort": "date", "page": 2}] * 2


def test_browse_actions(plugin):
    plugin.browse("latest", 3)
    plugin.browse("leaderboard", 1, mode="week")
    plugin.browse("category", 1, name="manga", sort="popular")
    plugin.browse("category", 1, name="unknown")
    plugin.browse("random")
    calls = [(call[1].rsplit("/", 1)[1], call[2]) for call in plugin.calls if call[1].endswith(("/search", "/galleries"))]
    assert calls[0] == ("galleries", {"page": 3})
    assert calls[1] == ("search", {"query": "pages:>0", "sort": "popular-week", "page": 1})
    assert calls[2] == ("search", {"query": 'category:"manga"', "sort": "popular", "page": 1})
    assert calls[3][1]["query"] == "pages:>0"
    assert calls[4][0] == "galleries" and 1 <= calls[4][1]["page"] <= 4


def test_errors_map_to_clear_codes(plugin):
    with pytest.raises(ComicApiError) as error:
        plugin.detail("404")
    assert error.value.status_code == 404
    with pytest.raises(ComicApiError) as error:
        plugin.detail("../etc")
    assert error.value.status_code == 404
    plugin.client.request = lambda *a, **k: Response(429, {"error": "slow down"})
    with pytest.raises(ComicApiError) as error:
        plugin.search("x")
    assert error.value.code == "rate_limited"


def test_api_key_login_validates_and_is_sent(plugin):
    assert plugin.account_status() == {"authenticated": False, "configured": False}
    with pytest.raises(ComicApiError) as error:
        plugin.login({"api_key": "bad"})
    assert error.value.code == "login_failed"
    assert plugin.account_status()["configured"] is False
    with pytest.raises(ComicApiError):
        plugin.login({"api_key": "has space"})
    plugin.login({"api_key": " good "})
    assert plugin.account_status() == {"authenticated": True, "configured": True}
    plugin.search("x")
    assert plugin.calls[-1][3]["Authorization"] == "Key good"
    plugin.logout()
    assert plugin.account_status()["configured"] is False


def _zip_bytes(names):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        for name in names:
            image = io.BytesIO()
            Image.new("RGB", (8, 12), "#336699").save(image, "PNG")
            bundle.writestr(name, image.getvalue())
        bundle.writestr("info.txt", "ignored")
    return data.getvalue()


def _archive_client(plugin, payload, seen):
    @contextmanager
    def stream(method, url, headers=None, **kwargs):
        seen.append((url, headers))
        yield Response(200, chunks=[payload[:10], payload[10:]])
    plugin.client.stream = stream


def test_archive_download_requires_key(plugin, tmp_path):
    with pytest.raises(ComicApiError) as error:
        plugin.download_archive("123", "123", str(tmp_path))
    assert error.value.code == "login_required"


def test_archive_download_extracts_images_in_natural_order(plugin, tmp_path, monkeypatch):
    plugin.store.save_account(plugin.id, {"api_key": "good"})
    monkeypatch.setattr("src.services.image_network.image_target", lambda url: None)
    seen = []
    _archive_client(plugin, _zip_bytes(["10.png", "2.png", "1.png", "../evil/3.png"]), seen)
    progress = []
    paths = plugin.download_archive("123", "123", str(tmp_path), progress=lambda *a: progress.append(a))
    assert [p.rsplit("/", 1)[1] for p in paths] == ["0001.png", "0002.png", "0003.png", "0004.png"]
    assert all(str(tmp_path) in p for p in paths)
    assert not (tmp_path / "archive.zip").exists()
    post = [call for call in plugin.calls if call[0] == "POST"][0]
    assert post[1].endswith("/galleries/123/download") and post[2] == {"format": "zip"}
    assert post[3]["Authorization"] == "Key good"
    assert seen[0][0].startswith("https://dl.nh.test/") and "Authorization" not in seen[0][1]
    assert progress[-1] == ("downloading", 4, 4)


def test_archive_rejects_corrupt_zip(plugin, tmp_path, monkeypatch):
    plugin.store.save_account(plugin.id, {"api_key": "good"})
    monkeypatch.setattr("src.services.image_network.image_target", lambda url: None)
    _archive_client(plugin, b"not a zip file at all", [])
    with pytest.raises(ComicApiError) as error:
        plugin.download_archive("123", "123", str(tmp_path))
    assert "损坏" in str(error.value)


def test_pdf_pipeline_uses_archive_instead_of_page_urls(tmp_path, monkeypatch):
    service = AggregatorService.__new__(AggregatorService)
    service._enabled = {}
    image = tmp_path / "0001.png"
    Image.new("RGB", (8, 12), "#336699").save(image)
    calls = []

    def archive(comic_id, chapter_id, out_dir, progress, cancelled):
        calls.append((comic_id, chapter_id))
        return [str(image)]

    service.sources = {"nhentai": SimpleNamespace(download_archive=archive)}

    async def no_pages(*args):
        raise AssertionError("page URLs must not be fetched")
    monkeypatch.setattr(service, "get_chapter_images", no_pages)

    path = asyncio.run(service.download_chapter_pdf("nhentai", "123", "123"))
    try:
        assert calls == [("123", "123")]
        with open(path, "rb") as stream:
            assert stream.read(4) == b"%PDF"
    finally:
        import os
        os.remove(path)


def test_manifest_exposes_login_hint_and_download_precheck(plugin):
    manifest = plugin.manifest()
    assert "API Key" in manifest["login_hint"] and "login" in manifest["capabilities"]
    with pytest.raises(ComicApiError) as error:
        plugin.check_download()
    assert error.value.status_code == 401
    plugin.store.save_account(plugin.id, {"api_key": "good"})
    plugin.check_download()


def test_download_queue_rejects_nhentai_without_key(tmp_path):
    from src.services.downloads import DownloadManager
    store = Store(tmp_path)
    service = AggregatorService.__new__(AggregatorService)
    service._enabled = {}
    service.sources = {"nhentai": NhentaiPlugin(store)}
    service.is_enabled = lambda source: True
    manager = DownloadManager(store, service)
    with pytest.raises(ComicApiError) as error:
        manager.create("nhentai", "123", "123")
    assert error.value.code == "login_required"
    assert store.tasks() == []
