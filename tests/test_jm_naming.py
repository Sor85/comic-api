import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from src.clients.jm import JmClient
from src.errors import ComicApiError
from src.services.jm_naming import JM_DIRECTORY_RULE, album_name, chapter_name, directory_name, original_name
from src.services.jm_sync import SyncSettings
from tests.test_jm_sync import isolated_environment, jm_client, make_sync


@pytest.mark.parametrize("title, expected", [
    ("[漢化組] [ひとにたち] ヒトメボレ [中国翻訳]", "ヒトメボレ"),
    ("中文名 [社团（作者）] 日本語 (雑誌) [DL版]", "中文名"),
    ("[作者 (社团[小组])] 完整 名称（补充）后续", "完整 名称"),
    ("【作者】（汉化） 原名 [DL版]", "原名"),
    ("[作者][翻译组]", "[作者][翻译组]"),
    ("   [作者] 标题...   ", "标题..."),
    ("作品 [未闭合 (内层) 尾部", "作品"),
    ("[未闭合 (内层) 尾部", "尾部"),
    ("[未闭合 标题", "[未闭合 标题"),
    ("a[x]b", "a"),
])
def test_original_name_matches_jmcomic_token_semantics(title, expected):
    assert original_name(title) == expected


# Golden paths verified by running JMComic 2.6.13 DirRule with commonX 0.6.40.
@pytest.mark.parametrize("title, authors, photo_title, sort, series_id, expected", [
    ("[漢化組] [ひとにたち] ヒトメボレ [中国翻訳]", ["ひとにたち", "第二作者"],
     "[漢化組] [ひとにたち] ヒトメボレ", 1, 101,
     ("【ひとにたち】ヒトメボレ", "第1話 [漢化組] [ひとにたち] ヒトメボレ")),
    ("中文名 [社团（作者）] 日本語 (雑誌) [DL版]", [" 第一作者 ", "第二作者"],
     " 第1話 完整标题 ", 2, 101, ("【 第一作者 】中文名", "第2話 第1話 完整标题")),
    ("[作者][翻译组]", [], "单章标题", 2, 0, ("【default_author】[作者][翻译组]", "第1話 单章标题")),
    ("作品 [未闭合 (内层) 尾部", ["a/b:c"], " 带/特殊:字符?标题... ", 12, 101,
     ("【a_b_c】作品", "第12話 带_特殊_字符_标题")),
    ("(only nested [brackets])", [""], "带空格的末尾. ", 1, 0,
     ("【】(only nested [brackets])", "第1話 带空格的末尾")),
])
def test_compatibility_paths_match_upstream_golden_cases(tmp_path, title, authors, photo_title, sort, series_id, expected):
    sync, plugin = make_sync(tmp_path)
    book = dict(plugin.detail("101"), title=title, authors=authors)
    chapter = {"id": "1011", "name": photo_title, "sort": sort, "series_id": series_id}
    config = dict(sync.settings(), directory_rule=JM_DIRECTORY_RULE)
    actual = sync.paths.chapter(config, book, chapter, 999)
    assert actual.relative_to(config["download_dir"]).parts == expected


@pytest.mark.parametrize("sort, series_id, expected", [(2, 0, 1), (2, 101, 2), (7, 101, 7), (1, 0, 1)])
def test_photo_index_uses_series_metadata_not_chapter_count(sort, series_id, expected):
    assert chapter_name({"name": "第1話 标题", "sort": str(sort), "series_id": str(series_id)}) == f"第{expected}話 第1話 标题"


def test_compatibility_preserves_long_names_without_appending_ids(tmp_path):
    sync, plugin = make_sync(tmp_path)
    title = "完整原标题" * 10
    book = dict(plugin.detail("101"), title=f"[汉化组] {title}", authors=["作者"])
    chapter = {"id": "1011", "name": "章节标题" * 12, "sort": "12", "series_id": "101"}
    config = dict(sync.settings(), directory_rule=JM_DIRECTORY_RULE)
    path = sync.paths.chapter(config, book, chapter, 1)
    assert path.parts[-2:] == (f"【作者】{title}", "第12話 " + "章节标题" * 12)
    chapter["name"] = "长" * 90
    with pytest.raises(ComicApiError, match="超过 255 字节"):
        sync.paths.chapter(config, book, chapter, 1)


def test_name_cleaning_preserves_upstream_order_and_author_whitespace():
    assert directory_name("  标题... ") == "标题..."
    assert directory_name("标题...") == "标题"
    assert directory_name('a/b\\c:d*e?f"g<h>i|j\n\t\r') == "a_b_c_d_e_f_g_h_i_j___"
    assert album_name({"title": "作品", "authors": [" a ", "b"], "author": "a/b"}) == "【 a 】作品"
    assert album_name({"title": "作品", "authors": []}) == "【default_author】作品"


@pytest.mark.parametrize("values", [{}, {"name": None, "sort": 1, "series_id": 1},
    {"name": "title", "sort": "invalid", "series_id": 1}, {"name": "title", "sort": 1, "series_id": None}])
def test_missing_photo_metadata_is_not_guessed(values):
    with pytest.raises(ComicApiError, match="原始标题或序号"):
        chapter_name(values)


def api_sync(tmp_path, single=False):
    sync, plugin = make_sync(tmp_path)
    sync.save_settings(SyncSettings(**dict(sync.settings(), directory_rule=JM_DIRECTORY_RULE, folder_ids=["7"])))
    client = JmClient(sync.store)
    photo_id = "101" if single else "1011"
    album = {"id": "101", "name": "[漢化組] [ひとにたち] ヒトメボレ [中文版]", "author": ["ひとにたち", "第二作者"],
             "series": [] if single else [{"id": photo_id, "name": "漫画详情中的简称", "sort": "1"}]}
    photos = {photo_id: {"id": photo_id, "name": " [漢化組] [ひとにたち] ヒトメボレ ",
                         "series_id": "0" if single else "101", "series": [{"id": photo_id, "sort": "2" if single else "7"}],
                         "images": ["00001.png", "00002.png"]}}
    calls = []
    def request(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return deepcopy(album if endpoint == "/album" else photos[kwargs["params"]["id"]])
    client.jm_request = request
    plugin.detail = client.get_comic_detail
    plugin.chapter_detail = client.get_chapter_detail
    plugin.pages = client.get_chapter_images
    return sync, plugin, client, album, photos, calls


@pytest.mark.parametrize("single", [False, True])
def test_download_uses_real_photo_metadata_and_first_author(tmp_path, single):
    async def scenario():
        sync, plugin, client, album, photos, calls = api_sync(tmp_path, single)
        detail = client.get_comic_detail("101")
        assert detail["authors"] == ["ひとにたち", "第二作者"]
        assert detail["author"] == "ひとにたち/第二作者"
        sync.launch(["101"])
        await sync.task
        assert sync.current["status"] == "completed", sync.current
        expected = Path(sync.settings()["download_dir"]) / "【ひとにたち】ヒトメボレ" / f"第{1 if single else 7}話 [漢化組] [ひとにたち] ヒトメボレ"
        assert sorted(path.name for path in expected.iterdir()) == ["00001.png", "00002.png"]
        assert len([entry for entry in calls if entry[0] == "/chapter"]) == 1
        assert sync.current["downloaded"] == 2
        sync.launch(["101"])
        await sync.task
        assert sync.current["skipped"] == 2 and len(plugin.requests) == 2
        assert not list(Path(sync.settings()["download_dir"]).rglob("*开始阅读*"))
        assert not list(Path(sync.settings()["download_dir"]).rglob("*简称*"))
    asyncio.run(scenario())


def test_photo_adapter_keeps_old_pages_contract_and_default_sort(tmp_path):
    sync, plugin, client, album, photos, calls = api_sync(tmp_path)
    photos["1011"]["series"] = [{"id": "999", "sort": "bad"}]
    data = client.get_chapter_detail("101", "1011")
    assert data["sort"] == 1
    assert client.get_chapter_images("101", "1011") == data["images"]
    assert all(entry[1]["params"] == {"id": "1011", "skip": ""} for entry in calls)
    client.jm_request = lambda *args, **kwargs: {"images": ["00001.png"]}
    assert len(client.get_chapter_images("101", "1011")) == 1
    client.jm_request = lambda *args, **kwargs: None
    assert client.get_chapter_images("101", "1011") == []


@pytest.mark.parametrize("field, value", [("id", None), ("name", None), ("series_id", None), ("series", None), ("series", [None])])
def test_incomplete_photo_metadata_never_creates_a_guessed_directory(tmp_path, field, value):
    async def scenario():
        sync, plugin, client, album, photos, calls = api_sync(tmp_path)
        photos["1011"][field] = value
        sync.launch(["101"])
        await sync.task
        assert sync.current["failed"] == 1 and sync.current["downloaded"] == 0
        assert not plugin.requests
        assert not list(Path(sync.settings()["download_dir"]).iterdir())
    asyncio.run(scenario())


def test_legacy_directory_import_and_new_chapters_share_exact_rule(tmp_path):
    async def scenario():
        sync, plugin, client, album, photos, calls = api_sync(tmp_path)
        root = Path(sync.settings()["download_dir"])
        target = root / "【ひとにたち】ヒトメボレ" / "第7話 [漢化組] [ひとにたち] ヒトメボレ"
        target.mkdir(parents=True)
        for page in (1, 2):
            (target / f"{page:05d}.png").write_bytes(plugin.picture)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in target.iterdir()}
        sync.launch(check_local=True)
        await sync.task
        assert sync.current["local_albums"] == 1 and sync.current["local_images"] == 2, sync.current
        assert not plugin.requests
        sync.launch(["101"])
        await sync.task
        assert sync.current["skipped"] == 2 and not plugin.requests
        album["series"].append({"id": "1012", "name": "第二章简称", "sort": "2"})
        photos["1012"] = {"id": "1012", "name": "第二章完整标题", "series_id": "101",
                          "series": [{"id": "1012", "sort": "8"}], "images": ["00001.png", "00002.png"]}
        sync.launch(["101"])
        await sync.task
        assert sync.current["skipped"] == 2 and sync.current["downloaded"] == 2
        assert (root / "【ひとにたち】ヒトメボレ" / "第8話 第二章完整标题" / "00001.png").is_file()
        assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in target.iterdir()} == before
    asyncio.run(scenario())


def test_translated_titles_with_same_canonical_name_are_not_imported(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        sync.save_settings(SyncSettings(**dict(sync.settings(), directory_rule=JM_DIRECTORY_RULE)))
        original = plugin.detail
        plugin.detail = lambda cid: dict(original(cid), title=f"[翻译组{cid}] [作者] 同一作品 [版本{cid}]", authors=["作者"])
        plugin.favorites = lambda **kwargs: {"items": [{"id": "101", "title": "列表简称一"}, {"id": "102", "title": "列表简称二"}],
                                            "has_next": False, "total": 2}
        directory = Path(sync.settings()["download_dir"]) / "【作者】同一作品"
        directory.mkdir(parents=True)
        sync.launch(check_local=True)
        await sync.task
        assert sync.current["failed"] == 2 and sync.current["local_albums"] == 0
        assert all("Aauthoroname" in entry["message"] for entry in sync.current["errors"])
        assert not plugin.requests
        with sync.store.connect() as db:
            assert not db.execute("SELECT * FROM jm_sync_local_files").fetchall()
    asyncio.run(scenario())


def test_same_title_with_different_authors_can_be_imported(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        sync.save_settings(SyncSettings(**dict(sync.settings(), directory_rule=JM_DIRECTORY_RULE)))
        original = plugin.detail
        plugin.detail = lambda cid: dict(original(cid), title="[汉化组] 同名作品", authors=[f"作者{cid}"])
        for cid in ("101", "102"):
            for index in (1, 2):
                target = Path(sync.settings()["download_dir"]) / f"【作者{cid}】同名作品" / f"第{index}話 第 {index} 章"
                target.mkdir(parents=True)
                for page in (1, 2):
                    (target / f"{page:05d}.png").write_bytes(plugin.picture)
        sync.launch(check_local=True)
        await sync.task
        assert sync.current["local_albums"] == 2 and not sync.current["failed"], sync.current
        assert not plugin.requests
    asyncio.run(scenario())


def test_incomplete_album_metadata_aborts_before_any_import(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        sync.save_settings(SyncSettings(**dict(sync.settings(), directory_rule=JM_DIRECTORY_RULE)))
        plugin.favorites = lambda **kwargs: {"items": [{"id": "101"}, {"id": "102"}], "has_next": False, "total": 2}
        original = plugin.detail
        def detail(cid):
            if cid == "102":
                raise ComicApiError("模拟详情读取失败", 502, "upstream_error", "jm")
            return original(cid)
        plugin.detail = detail
        Path(sync.settings()["download_dir"]).mkdir()
        sync.launch(check_local=True)
        await sync.task
        assert sync.current["status"] == "failed" and sync.current["local_albums"] == 0
        assert "详情读取失败" in sync.current["error"] and not plugin.requests
    asyncio.run(scenario())


def test_canonical_directory_conflict_never_mixes_another_album(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        sync.save_settings(SyncSettings(**dict(sync.settings(), directory_rule=JM_DIRECTORY_RULE)))
        original = plugin.detail
        plugin.detail = lambda cid: dict(original(cid), title=f"[版本{cid}] 相同原标题", authors=["同一作者"])
        sync.launch(["101"])
        await sync.task
        assert sync.current["downloaded"] == 4
        root = Path(sync.settings()["download_dir"])
        before = {p: p.read_bytes() for p in root.rglob("*.png")}
        sync.launch(["102"])
        await sync.task
        assert sync.current["failed"] == 1 and "未混合写入" in sync.current["errors"][0]["message"]
        assert {p: p.read_bytes() for p in root.rglob("*.png")} == before
        assert len(plugin.requests) == 4
    asyncio.run(scenario())


def test_exact_compatibility_option_is_exposed_and_saved(jm_client):
    options = jm_client.get("/api/jm/sync/settings").json()
    assert options["directory_rules"][0]["value"] == JM_DIRECTORY_RULE
    settings = dict(options["settings"], directory_rule=JM_DIRECTORY_RULE)
    assert jm_client.put("/api/jm/sync/settings", json=settings).status_code == 200
    assert jm_client.get("/api/jm/sync/settings").json()["settings"]["directory_rule"] == JM_DIRECTORY_RULE
    assert f'value="{JM_DIRECTORY_RULE}">【作者】原标题 / 第N話 章节标题</option>' in jm_client.get("/").text
    assert "【作者】原标题/第1話 章节标题" in jm_client.get("/static/jm-sync.js").text
