import pytest
from fastapi.testclient import TestClient

from src.clients.bika import BikaClient
from src.clients.jm import JmClient
from src.errors import ComicApiError
from src.metadata import comic_labels
from src.services.aggregator import parse_tag_query
from src.sources import SourcePlugin
from src.sources.copy import CopyPlugin
from src.sources.jm import JmPlugin
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


@pytest.mark.parametrize('author', ['123456', 'jm作者', 'A&B / 中文'])
def test_jm_author_search_uses_author_scope_and_never_id_shortcut(author):
    client, calls = jm_client({'data': {'content': [{'id': '1', 'name': '标题'}]}})
    for page in (1, 3):
        assert client.search_author(author, page)[0]['id'] == '1'
        assert calls[-1] == ('/search', {'method': 'GET', 'params': {
            'main_tag': '2', 'search_query': author, 'page': str(page)}})
    assert len(calls) == 2


def test_jm_author_search_rejects_invalid_response():
    client, _ = jm_client({'data': []})
    with pytest.raises(ComicApiError) as error:
        client.search_author('作者')
    assert error.value.code == 'invalid_response'


def test_bika_author_search_uses_native_author_filter_with_sort_and_category():
    client, calls = bika_client({'data': {'comics': {'docs': [{'_id': '1', 'title': '标题'}]}}})
    assert client.search_author('作者 A&B / 名称', 3, 'vd', '全彩')[0]['id'] == '1'
    assert calls == [('comics', {'method': 'GET', 'params': {
        'page': '3', 'a': '作者 A&B / 名称', 's': 'vd', 'c': '全彩'}})]
    client.search_author('作者')
    assert calls[-1][1]['params'] == {'page': '1', 'a': '作者', 's': 'dd'}
    client.ensure_authenticated = lambda: (_ for _ in ()).throw(ComicApiError('需要登录', 401, 'login_required'))
    with pytest.raises(ComicApiError):
        client.search_author('作者')
    assert len(calls) == 2


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
        if source != 'copy':
            self.capabilities = [*self.capabilities, 'author_search']
        if source == 'bika':
            self.capabilities = [*self.capabilities, 'search_categories']
            self.local_tag_filter = True
        if source != 'copy':
            self.capabilities = [*self.capabilities, 'multi_tag_search']

    def search(self, keyword, page=1, **kwargs):
        self.calls.append(('keyword', keyword, page, kwargs))
        return [{'id': '1', 'title': keyword}]

    def search_tag(self, keyword, page=1, **kwargs):
        self.calls.append(('tag', keyword, page, kwargs))
        if self.id == 'copy' and keyword == '部分失败':
            raise ComicApiError('未知题材', 422, 'unknown_tag')
        if self.local_tag_filter and keyword == '巨乳':
            return [{'id': '1', 'title': '缺少标签', 'tags': [{'label': keyword, 'value': keyword}]},
                    {'id': '2', 'title': '命中', 'tags': ['巨乳', 'Glasses'], 'categories': [{'label': '全彩', 'value': '全彩'}]},
                    {'id': '3', 'title': '含排除标签', 'tags': ['巨乳', 'glasses', '中文']}]
        return [{'id': '1', 'title': '非标签同名标题'}, {'id': '2', 'title': keyword}]

    def search_author(self, keyword, page=1, **kwargs):
        self.calls.append(('author', keyword, page, kwargs))
        if self.id == 'bika' and keyword == '部分失败':
            raise ComicApiError('需要登录', 401, 'login_required')
        return [{'id': '1', 'title': '非作者同名标题'}, {'id': '2', 'title': keyword}]

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


def test_author_api_keeps_native_order_and_passes_filters(search_api):
    client, plugins = search_api
    response = client.get('/api/search', params={'keyword': '作者', 'mode': 'author', 'source': 'bika',
                                                'category': '全彩', 'sort': 'dd', 'page': 3})
    assert response.status_code == 200
    assert [item['id'] for item in response.json()['items']] == ['1', '2']
    assert plugins['bika'].calls == [('author', '作者', 3, {'category': '全彩', 'sort': 'dd'})]
    assert client.get('/api/search', params={'keyword': '作者', 'mode': 'author', 'source': 'copy'}).status_code == 422
    assert plugins['copy'].calls == []


def test_aggregate_author_search_excludes_unsupported_sources_and_keeps_partial_errors(search_api):
    client, plugins = search_api
    response = client.get('/api/search', params={'keyword': '部分失败', 'mode': 'author', 'page': 2})
    assert response.status_code == 200
    data = response.json()
    assert list(data['all_results']) == ['jm', 'bika']
    assert [item['id'] for item in data['items']] == ['1', '2']
    assert data['errors']['bika']['code'] == 'login_required'
    assert plugins['jm'].calls == [('author', '部分失败', 2, {})]
    assert plugins['copy'].calls == []


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
    {'source': 'jm', 'mode': 'author', 'tag_value': '旧标签'},
    {'source': 'bika', 'mode': 'author', 'sort': 'vd'},
    {'mode': 'author', 'category': '全彩'},
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


def test_tag_query_parser_splits_quotes_and_exclusions():
    assert parse_tag_query(' 巨乳，眼鏡、全彩 -中文 +NTR "big breasts" -"full color" ') == (
        ['巨乳', '眼鏡', '全彩', 'NTR', 'big breasts'], ['中文', 'full color'])
    assert parse_tag_query('tag:"big breasts" -language:"chinese" 巨乳 巨乳 - + ""') == (
        ['tag:"big breasts"', '巨乳'], ['language:"chinese"'])
    assert parse_tag_query('weird" OR name') == (['weird', 'OR', 'name'], [])


def test_multi_tag_api_passes_native_terms_and_filters_locally(search_api):
    client, plugins = search_api
    response = client.get('/api/search', params={'keyword': '巨乳 -中文', 'mode': 'tag', 'source': 'jm', 'page': 2})
    assert response.status_code == 200
    assert plugins['jm'].calls == [('tag', '巨乳 -中文', 2, {'tags': ['巨乳'], 'exclude_tags': ['中文']})]
    response = client.get('/api/search', params={'keyword': '巨乳 glasses 全彩 -中文', 'mode': 'tag', 'source': 'bika',
                                                 'category': '全彩', 'sort': 'vd'}).json()
    # Upstream receives only the first tag; remaining tags match tags or categories case-insensitively.
    assert plugins['bika'].calls == [('tag', '巨乳', 1, {'category': '全彩', 'sort': 'vd'})]
    assert [item['id'] for item in response['items']] == ['2']
    assert response['upstream_count'] == 3


def test_single_tag_and_tag_links_keep_the_plain_tag_search(search_api):
    client, plugins = search_api
    client.get('/api/search', params={'keyword': '巨乳', 'mode': 'tag', 'source': 'bika'})
    client.get('/api/search', params={'keyword': '巨乳 -中文', 'mode': 'tag', 'source': 'bika', 'tag_value': 'x'})
    assert plugins['bika'].calls == [('tag', '巨乳', 1, {}), ('tag', '巨乳 -中文', 1, {'tag_value': 'x'})]
    response = client.get('/api/search', params={'keyword': '巨乳 -中文', 'mode': 'keyword', 'source': 'jm'})
    assert response.status_code == 200 and plugins['jm'].calls == [('keyword', '巨乳 -中文', 1, {})]


def test_multi_tag_search_rejects_unsupported_sources_and_exclusion_only(search_api):
    client, plugins = search_api
    response = client.get('/api/search', params={'keyword': '科幻 冒险', 'mode': 'tag', 'source': 'copy'})
    assert response.status_code == 422 and response.json()['error']['code'] == 'unsupported'
    for params in ({'source': 'jm'}, {}):
        response = client.get('/api/search', params={'keyword': '-中文 -NTR', 'mode': 'tag', **params})
        assert response.status_code == 422
    response = client.get('/api/search', params={'keyword': '巨乳 -中文', 'mode': 'tag'}).json()
    assert list(response['all_results']) == ['jm', 'bika']
    assert response['upstream_count'] == 5 and [item['id'] for item in response['items']] == ['1', '2', '1', '2']
    assert plugins['copy'].calls == []


def test_jm_multi_tag_search_requires_every_tag_and_excludes_with_minus():
    client, calls = jm_client({'data': {'content': []}})
    plugin = JmPlugin.__new__(JmPlugin)
    plugin.client = client
    plugin.search_tag('ignored', 3, tags=['巨乳', '眼鏡'], exclude_tags=['中文'])
    plugin.search_tag('全彩', tag_value='全彩标识')
    assert [call[1]['params']['search_query'] for call in calls] == ['+巨乳 +眼鏡 -中文', '全彩标识']
    assert calls[0][1]['params']['page'] == '3' and calls[0][1]['params']['main_tag'] == '3'
