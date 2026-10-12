import pytest
from fastapi.testclient import TestClient

from src.clients.bika import BikaClient
from src.clients.jm import JmClient
from src.errors import ComicApiError
from src.metadata import comic_labels
from src.sources import SourcePlugin
from src.sources.copy import CopyPlugin
from src.storage import Store


def test_labels_preserve_names_and_identifiers_without_splitting():
    assert comic_labels([' 全彩 ', '全彩', 'A/B', None, {}, 3]) == [
        {'label': '全彩', 'value': '全彩'}, {'label': 'A/B', 'value': 'A/B'}]
    assert comic_labels([{'name': '科幻', 'path_word': 'kehuan'}, {'name': '科幻', 'path_word': 'kehuan'},
                         {'name': '缺少标识'}, '不能冒充标识'], 'path_word') == [{'label': '科幻', 'value': 'kehuan'}]
    assert comic_labels(None) == comic_labels('全彩') == []


def jm_client(response):
    client = JmClient.__new__(JmClient)
    client.image_base = 'https://images.invalid'
    calls = []
    def request(path, **kwargs):
        calls.append((path, kwargs))
        return response
    client.jm_request = request
    return client, calls


def bika_client(response):
    client = BikaClient.__new__(BikaClient)
    client.ensure_authenticated = lambda: None
    calls = []
    def request(path, **kwargs):
        calls.append((path, kwargs))
        if '/eps' in path:
            return {'data': {'eps': {'docs': []}}}
        return response
    client.bika_request = request
    return client, calls


@pytest.mark.parametrize('tag', ['123456', 'jm测试', '全彩', 'A&B / 中文'])
def test_jm_tag_search_uses_tag_scope_and_bypasses_id_shortcut(tag):
    client, calls = jm_client({'data': {'content': [{'id': '1', 'name': '标题', 'tags': [tag]}]}})
    result = client.search_tag(tag, page=2)
    assert calls == [('/search', {'method': 'GET', 'params': {'main_tag': '3', 'search_query': tag, 'page': '2'}})]
    assert result[0]['tags'] == [{'label': tag, 'value': tag}]
    calls.clear()
    client.search_tag(tag, page=1)
    assert len(calls) == 1 and calls[0][0] == '/search'


def test_jm_detail_and_id_search_keep_tags():
    client, _ = jm_client({'data': {'id': '123', 'name': '标题', 'author': [], 'tags': ['全彩'], 'series': []}})
    assert client.get_comic_detail('123')['tags'] == [{'label': '全彩', 'value': '全彩'}]
    assert client.search('123')[0]['tags'] == [{'label': '全彩', 'value': '全彩'}]


def test_bika_detail_and_list_keep_tags_separate_from_categories():
    raw = {'_id': '1', 'title': '标题', 'tags': ['职场', '职场'], 'categories': ['全彩', '同人']}
    client, _ = bika_client({'data': {'comic': raw}})
    detail = client.get_comic_detail('1')
    item = client._parse_comics_list({'data': {'comics': {'docs': [raw]}}})[0]
    for row in (detail, item):
        assert row['tags'] == [{'label': '职场', 'value': '职场'}]
        assert row['categories'] == [{'label': '全彩', 'value': '全彩'}, {'label': '同人', 'value': '同人'}]
    assert item['category'] == '全彩 · 同人'


def test_bika_keyword_search_sends_categories_array():
    client, calls = bika_client({'data': {'comics': {'docs': []}}})
    client.search('词语', 3, 'vd', category='全彩')
    assert calls == [('comics/advanced-search', {'method': 'POST', 'params': {'page': '3'},
                    'json_body': {'keyword': '词语', 'sort': 'vd', 'categories': ['全彩']}})]
    client.search('词语')
    assert calls[-1][1]['json_body']['categories'] == []


def test_bika_tag_search_uses_native_tag_with_optional_category():
    client, calls = bika_client({'data': {'comics': {'docs': []}}})
    client.search_tag('职场', 2, 'ld', '全彩')
    assert calls == [('comics', {'method': 'GET', 'params': {'page': '2', 't': '职场', 's': 'ld', 'c': '全彩'}})]
    client.search_tag('职场')
    assert 'c' not in calls[-1][1]['params']


def test_bika_category_catalog_uses_titles_and_skips_web_links():
    client, calls = bika_client({'data': {'categories': [
        {'title': '全彩'}, {'title': '新分类'}, {'title': '网站', 'isWeb': True}, {'title': '全彩'}, None]}})
    assert client.search_categories() == [{'label': '全彩', 'value': '全彩'}, {'label': '新分类', 'value': '新分类'}]
    assert calls == [('categories', {'method': 'GET'})]


def test_copy_theme_detail_and_direct_tag_search_do_not_search_titles(tmp_path, monkeypatch):
    plugin = CopyPlugin(Store(tmp_path))
    calls = []
    def get(path, **params):
        calls.append((path, params))
        if path.startswith('comic2/'):
            return {'comic': {'path_word': 'book', 'name': '标题', 'theme': [{'name': '特殊题材', 'path_word': 'special'}]}}
        return {'list': []}
    monkeypatch.setattr(plugin, '_get', get)
    assert plugin.detail('book')['tags'] == [{'label': '特殊题材', 'value': 'special'}]
    plugin.search_tag('特殊题材', 3, '-popular', tag_value='special')
    assert calls[-1] == ('comics', {'theme': 'special', 'ordering': '-popular', 'free_type': 1, 'limit': 20, 'offset': 40, 'platform': 3})
    assert not any(path == 'search/comic' for path, _ in calls)


def test_copy_resolves_typed_theme_names_and_caches_catalog(tmp_path, monkeypatch):
    plugin = CopyPlugin(Store(tmp_path))
    calls = []
    def get(path, **params):
        calls.append((path, params))
        if path == 'h5/filter/comic/tags':
            return {'theme': [{'name': '侦探', 'path_word': 'zhentan'}]}
        return {'list': []}
    monkeypatch.setattr(plugin, '_get', get)
    plugin.search_tag('侦探')
    plugin.search_tag('zhentan', page=2)
    assert calls[0] == ('h5/filter/comic/tags', {'type': 1, 'platform': 3})
    assert len([path for path, _ in calls if path == 'h5/filter/comic/tags']) == 1
    assert calls[-1][1]['theme'] == 'zhentan' and calls[-1][1]['offset'] == 20
    with pytest.raises(ComicApiError) as error:
        plugin.search_tag('不存在的题材')
    assert error.value.code == 'unknown_tag'
    assert not any(path == 'search/comic' for path, _ in calls)


@pytest.mark.parametrize('theme', ['科幻', 'kehuan'])
def test_copy_known_theme_does_not_need_catalog(tmp_path, monkeypatch, theme):
    plugin = CopyPlugin(Store(tmp_path))
    calls = []
    monkeypatch.setattr(plugin, '_get', lambda path, **params: calls.append((path, params)) or {'list': []})
    plugin.search_tag(theme)
    assert len(calls) == 1 and calls[0][0] == 'comics'
    assert calls[0][1]['theme'] == 'kehuan'


class SearchPlugin(SourcePlugin):
    capabilities = ['search', 'tag_search', 'detail']
    search_sorts = [{'value': 'dd', 'label': '最新'}]
    tag_sorts = [{'value': 'vd', 'label': '热门'}]

    def __init__(self, store, source):
        super().__init__(store)
        self.id = self.name = source
        self.calls = []
        if source == 'bika':
            self.capabilities = [*self.capabilities, 'search_categories']

    def search(self, keyword, page=1, **kwargs):
        self.calls.append(('keyword', keyword, page, kwargs))
        return [{'id': '1', 'title': keyword}]

    def search_tag(self, keyword, page=1, **kwargs):
        self.calls.append(('tag', keyword, page, kwargs))
        if self.id == 'copy' and keyword == '部分失败':
            raise ComicApiError('未知题材', 422, 'unknown_tag')
        return [{'id': '1', 'title': '非标签同名标题'}, {'id': '2', 'title': keyword}]

    def search_categories(self):
        return [{'label': '全彩', 'value': '全彩'}]


@pytest.fixture
def search_api(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    from main import create_app
    store = Store(tmp_path)
    plugins = {source: SearchPlugin(store, source) for source in ('jm', 'bika', 'copy')}
    with TestClient(create_app(store, plugins)) as client:
        yield client, plugins


def test_tag_api_keeps_native_order_and_passes_filters(search_api):
    client, plugins = search_api
    response = client.get('/api/search', params={'keyword': '科幻', 'mode': 'tag', 'source': 'bika',
                                                 'tag_value': '科幻标签', 'category': '全彩', 'sort': 'vd', 'page': 3})
    assert response.status_code == 200
    assert [item['id'] for item in response.json()['items']] == ['1', '2']
    assert plugins['bika'].calls == [('tag', '科幻', 3, {'tag_value': '科幻标签', 'category': '全彩', 'sort': 'vd'})]
    client.get('/api/search', params={'keyword': '科幻', 'source': 'bika', 'category': '全彩'})
    assert plugins['bika'].calls[-1] == ('keyword', '科幻', 1, {'category': '全彩'})


def test_aggregate_tag_search_preserves_partial_errors(search_api):
    client, plugins = search_api
    response = client.get('/api/search', params={'keyword': '部分失败', 'mode': 'tag'}).json()
    assert len(response['items']) == 4
    assert response['errors']['copy']['code'] == 'unknown_tag'
    assert all(plugin.calls[0][0] == 'tag' for plugin in plugins.values())


@pytest.mark.parametrize('params', [
    {'mode': 'other'}, {'keyword': '   '}, {'tag_value': 'value'},
    {'mode': 'tag', 'tag_value': 'value'}, {'category': '全彩'},
    {'source': 'jm', 'category': '全彩'}, {'source': 'copy', 'category': '全彩'},
    {'source': 'bika', 'mode': 'tag', 'sort': 'dd'}, {'source': 'bika', 'sort': 'vd'},
])
def test_invalid_filter_combinations_fail_before_upstream(search_api, params):
    client, plugins = search_api
    response = client.get('/api/search', params={'keyword': '测试', **params})
    assert response.status_code == 422
    assert all(not plugin.calls for plugin in plugins.values())


def test_category_catalog_and_tag_capabilities_are_exposed(search_api):
    client, _ = search_api
    assert client.get('/api/sources/bika/search-categories').json() == {'categories': [{'label': '全彩', 'value': '全彩'}]}
    assert client.get('/api/sources/jm/search-categories').status_code == 422
    manifests = client.get('/api/sources').json()['sources']
    assert all('tag_search' in item['capabilities'] and item['tag_sorts'] for item in manifests)


def test_older_keyword_plugin_signature_is_unchanged(search_api):
    client, plugins = search_api
    plugin = plugins['jm']
    plugin.capabilities = ['search']
    def old_search(keyword, page=1):
        return [{'id': 'old', 'title': keyword}]
    plugin.search = old_search
    assert client.get('/api/search', params={'keyword': '测试', 'source': 'jm'}).status_code == 200
    assert client.get('/api/search', params={'keyword': '测试', 'source': 'jm', 'mode': 'tag'}).status_code == 422
