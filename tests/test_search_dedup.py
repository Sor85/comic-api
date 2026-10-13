from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from src.clients.bika import BikaClient
from src.clients.jm import JmClient
from src.metadata import comic_authors, comic_page_count
from src.services.search_dedup import deduplicate_results, search_result_payload
from src.sources.copy import CopyPlugin
from src.storage import Store
from tests.fakes import DemoPlugin


def book(comic_id='1', source='jm', **fields):
    return {'id': comic_id, 'source': source, 'title': 'Summer Letters', 'author': 'Alice', **fields}


def test_duplicates_keep_first_rank_and_all_distinct_versions_without_mutation():
    items = [book(title='[Alice] Ｓｕｍｍｅｒ Letters [中文]', page_count=100),
             book('2', 'bika', title='summer-letters', author='ＡＬＩＣＥ', page_count=102),
             book('3', title='Summer Letters 2'), book(title='repeated upstream ID')]
    before = deepcopy(items)
    result = deduplicate_results(items)
    assert len(result) == 2
    assert result[0]['id'] == '1'
    assert result[0]['variants'] == items[:2]
    assert result[1] == items[2]
    assert items == before
    assert all('variants' not in item for item in result[0]['variants'])


@pytest.mark.parametrize('changes', [
    {'author': 'Bob'}, {'authors': ['Alice', 'Bob']}, {'page_count': 80},
    {'title': 'Summer Letters 2'}, {'title': 'Summer Letter'},
    {'title': 'Summer Letters 上'}, {'title': 'Summer Letters 番外'},
    {'title': 'Summer Letters [完整版]'}, {'title': 'Summer Letters [第2卷]'},
    {'title': 'Summer Letters (另一部作品)'},
])
def test_conflicting_metadata_and_different_editions_stay_separate(changes):
    assert len(deduplicate_results([book(page_count=40), book('2', page_count=40) | changes])) == 2


@pytest.mark.parametrize('first,second', [
    ('Summer Letters 1-2', 'Summer Letters 12'),
    ('Summer Letters 1.2', 'Summer Letters 12'),
    ('Summer Letters 1-2', 'Summer Letters 1.2'),
    ('Summer Letters 1-2', 'Summer Letters 1/2'),
    ('Summer Letters 01', 'Summer Letters 1'),
    ('Summer Letters 上卷', 'Summer Letters 下卷'),
    ('Summer Letters [汉化 第三卷]', 'Summer Letters'),
])
def test_volume_markers_are_not_lost_to_punctuation_or_bracket_cleanup(first, second):
    assert len(deduplicate_results([book(title=first), book('2', title=second)])) == 2


def test_author_sets_are_order_independent_but_not_partial_matches():
    first = book(authors=['Alice', 'Bob'])
    second = book('2', authors=[{'name': 'ＢＯＢ'}, 'alice'])
    third = book('3', author='Alice')
    result = deduplicate_results([first, second, third])
    assert len(result) == 2 and len(result[0]['variants']) == 2


def test_title_and_author_are_enough_when_work_page_counts_are_not_available():
    result = deduplicate_results([book(), book('2', 'copy', authors=['Alice'])])
    assert len(result) == 1
    assert len(result[0]['variants']) == 2


@pytest.mark.parametrize('author', ['', None, '佚名', '未知作者', 'N/A', 'unknown'])
def test_missing_authors_require_equal_real_page_counts(author):
    assert len(deduplicate_results([book(author=author), book('2', author=author)])) == 2
    assert len(deduplicate_results([book(author=author, page_count=40), book('2', author=author, page_count=40)])) == 1
    assert len(deduplicate_results([book(author=author, page_count=40), book('2', author=author, page_count=41)])) == 2
    assert len(deduplicate_results([book(author=author, title='同名', page_count=40),
                                    book('2', author=author, title='同名', page_count=40)])) == 2


@pytest.mark.parametrize('title', ['', '无题', '未命名', 'Untitled', '合集', '短篇'])
def test_generic_or_empty_titles_are_not_cross_book_identity(title):
    assert len(deduplicate_results([book(title=title), book('2', title=title)])) == 2


def test_known_author_conflict_is_not_overruled_by_equal_pages():
    assert len(deduplicate_results([book(page_count=40), book('2', author='Bob', page_count=40)])) == 2


def test_missing_metadata_cannot_bridge_conflicting_authors_or_page_counts():
    alice, unknown, bob = book(page_count=40), book('2', author='', page_count=40), book('3', author='Bob', page_count=40)
    result = deduplicate_results([alice, unknown, bob])
    assert len(result) == 2 and result[1] == bob
    # When either match would be plausible, do not choose one arbitrarily.
    assert len(deduplicate_results([alice, bob, unknown])) == 3
    result = deduplicate_results([book(page_count=100), book('2', page_count=103), book('3', page_count=106)])
    assert len(result) == 2 and len(result[0]['variants']) == 2


def test_default_payload_does_not_even_remove_repeated_source_ids():
    items = [book(), book(), book('2')]
    raw = search_result_payload(items, {'jm': items}, {})
    assert raw['items'] == items and raw['raw_count'] == 3 and raw['duplicate_count'] == 0
    grouped = search_result_payload(items, {'jm': items}, {}, deduplicate=True)
    assert len(grouped['items']) == 1 and grouped['duplicate_count'] == 2
    assert grouped['all_results'] == raw['all_results']


def test_same_source_id_collapses_but_equal_ids_in_different_sources_do_not():
    first = book(author='')
    assert deduplicate_results([first, dict(first)]) == [first]
    assert len(deduplicate_results([first, book(source='bika', author='')])) == 2
    assert len(deduplicate_results([book(id=None, title=''), book(id=None, title='')])) == 2


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
@pytest.mark.parametrize('deduplicate', [None, False, True])
def test_api_groups_single_and_aggregate_pages_only_when_enabled(tmp_path, monkeypatch, mode, deduplicate):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    from main import create_app
    store = Store(tmp_path)
    plugins, calls = {}, []
    for source in ('jm', 'bika'):
        plugin = DemoPlugin(store)
        plugin.id = source
        plugin.capabilities = ['search', 'tag_search']
        def search(keyword, page=1, source=source):
            calls.append((source, keyword, page))
            if page == 3:
                return []
            return [book('1', source), book('2', source), book('3', source, title='Another Story')]
        plugin.search = plugin.search_tag = search
        plugin.detail = lambda _: pytest.fail('Search grouping must not fetch details')
        plugins[source] = plugin
    params = {'keyword': 'Summer', 'mode': mode}
    if deduplicate is not None:
        params['deduplicate'] = deduplicate
    with TestClient(create_app(store, plugins)) as client:
        for page in (1, 2):
            result = client.get('/api/search', params={**params, 'page': page}).json()
            assert len(result['items']) == (2 if deduplicate else 6)
            assert result['raw_count'] == 6 and result['duplicate_count'] == (4 if deduplicate else 0)
            if deduplicate:
                assert len(result['items'][0]['variants']) == 4
            else:
                assert all('variants' not in item for item in result['items'])
            assert len(result['all_results']['jm']) == len(result['all_results']['bika']) == 3
            assert result['best_match'] == result['items'][0]
        result = client.get('/api/search', params={**params, 'source': 'jm'}).json()
        assert len(result['items']) == (2 if deduplicate else 3)
        assert result['duplicate_count'] == (1 if deduplicate else 0)
        assert result['raw_count'] == 3 and len(result['all_results']['jm']) == 3
        empty = client.get('/api/search', params={**params, 'page': 3}).json()
        assert empty['items'] == [] and empty['best_match'] is None and empty['raw_count'] == 0
    assert len(calls) == 7
    assert sorted(page for _, _, page in calls) == [1, 1, 1, 2, 2, 3, 3]
