import pytest
from fastapi.testclient import TestClient

from src.clients.bika import BikaClient
from src.clients.jm import JmClient
from src.metadata import comic_authors, comic_page_count
from src.sources.copy import CopyPlugin
from src.storage import Store
from tests.fakes import DemoPlugin


def book(comic_id='1', source='jm', **fields):
    return {'id': comic_id, 'source': source, 'title': 'Summer Letters', 'author': 'Alice', **fields}


def test_author_metadata_handles_old_strings_and_structured_names():
    assert comic_authors(' Alice / Bob、Alice ') == ['Alice', 'Bob']
    assert comic_authors(['A/B', {'name': ' Bob '}, 'bob', None, 7, {'name': 3}]) == ['A/B', 'Bob']
    assert comic_authors('N/A') == comic_authors(['未知作者']) == []


@pytest.mark.parametrize('value', [None, False, True, 0, -1, 4.5, 40.0, '40页', '1.5', [], {}, '0'])
def test_invalid_page_counts_are_unknown_not_zero(value):
    assert comic_page_count(value) is None


def test_page_count_mapping_does_not_use_chapter_or_api_pagination_counts(tmp_path):
    jm = JmClient.__new__(JmClient)
    jm.image_base = 'https://images.invalid'
    raw = {'id': '123', 'name': 'Summer Letters', 'author': ['Alice', 'Bob'], 'total_photo': '42'}
    jm.jm_request = lambda *args, **kwargs: {'data': raw}
    for item in (jm._parse_jm_comics([raw])[0], jm.get_comic_detail('123'), jm.search('123')[0]):
        assert item['authors'] == ['Alice', 'Bob']
        assert item['page_count'] == 42
        assert isinstance(item['author'], str)
    bika = BikaClient.__new__(BikaClient)
    bika.ensure_authenticated = lambda: None
    raw = {'_id': '123', 'title': 'Summer Letters', 'author': 'Alice / Bob', 'pagesCount': '43', 'epsCount': 2}
    bika.bika_request = lambda path, **kwargs: {'data': {'comic': raw, 'eps': {'docs': []}}}
    for item in (bika.get_comic_detail('123'), bika._parse_comics_list({'data': {'comics': {'docs': [raw], 'pages': 10}}})[0]):
        assert item['authors'] == ['Alice', 'Bob'] and item['page_count'] == 43
    raw.pop('pagesCount')
    assert bika.get_comic_detail('123')['page_count'] is None
    assert bika._parse_comics_list({'data': {'comics': {'docs': [raw], 'pages': 10}}})[0]['page_count'] is None
    copy = CopyPlugin(Store(tmp_path))
    item = copy._comic({'path_word': '123', 'author': [{'name': 'Alice'}, {'name': 'Bob'}], 'chapters': 9})
    assert item['authors'] == ['Alice', 'Bob'] and item.get('page_count') is None


@pytest.mark.parametrize('mode', ['keyword', 'tag'])
def test_api_keeps_every_source_result_without_grouping(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    from main import create_app
    store = Store(tmp_path)
    plugins = {}
    for source in ('jm', 'bika'):
        plugin = DemoPlugin(store)
        plugin.id = source
        plugin.capabilities = ['search', 'tag_search']
        plugin.search = plugin.search_tag = lambda keyword, page=1, source=source: (
            [] if page == 2 else [book('1', source), book('1', source), book('3', source, title='Another Story')])
        plugins[source] = plugin
    params = {'keyword': 'Summer', 'mode': mode, 'deduplicate': True}
    with TestClient(create_app(store, plugins)) as client:
        result = client.get('/api/search', params=params).json()
        assert len(result['items']) == result['upstream_count'] == 6
        assert all('variants' not in item for item in result['items'])
        assert 'raw_count' not in result and 'duplicate_count' not in result
        assert result['best_match'] == result['items'][0]
        result = client.get('/api/search', params={**params, 'source': 'jm'}).json()
        assert len(result['items']) == result['upstream_count'] == 3
        empty = client.get('/api/search', params={**params, 'page': 2}).json()
        assert empty['items'] == [] and empty['best_match'] is None and empty['upstream_count'] == 0
