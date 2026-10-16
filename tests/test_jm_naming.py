import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from src.clients.jm import JmClient
from src.errors import ComicApiError
from src.services.jm_naming import JM_DIRECTORY_RULE, album_name, chapter_directory, chapter_name, directory_name, original_name
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
    cut = sync.paths.chapter(config, book, chapter, 1)
    assert cut.parent == path.parent and ("第12話 " + "长" * 90).startswith(cut.name) and len(cut.name.encode()) <= 255


LONG_TITLE = ("魔物棲息之所 時隔6年再會被已經變成家里蹲的朋友的姐姐要挾然後被榨取精液的故事 [白楊漢化組][鄰居toby去碼] [桃汁庭園 (桃雲)] "
              "マモノノヘヤ ～6年ぶりに再會したら引きこもりニートになっていた親友のお姉さんに迫られて精液を搾り取られる話～ [中國翻譯] [無修正] [DL版]")


def test_over_long_chapter_name_is_cut_like_jmcomic_save_path():
    chapter = {"id": "101", "name": LONG_TITLE, "sort": "2", "series_id": "0"}
    full = f"第1話 {LONG_TITLE}"
    assert len(full.encode()) == 378
    # JMComic 2.6.13 (JmcomicText.try_mkdir) retries the save path cut to its first 100 characters.
    assert chapter_directory("/data/本子/【桃雲】魔物棲息之所", chapter) == (
        "第1話 魔物棲息之所 時隔6年再會被已經變成家里蹲的朋友的姐姐要挾然後被榨取精液的故事 [白楊漢化組][鄰居toby去碼] [桃汁庭園 (桃雲)] マモノノヘヤ")
    # When that cut would drop the 第N話 label, only the name is cut to the 255-byte filesystem limit.
    deep = chapter_directory("/data/" + "子" * 95, chapter)
    assert full.startswith(deep) and len(deep.encode()) == 253
    short = {"id": "102", "name": "短标题", "sort": "1", "series_id": "101"}
    assert chapter_directory("/data/" + "子" * 95, short) == "第1話 短标题"
    assert album_name({"title": "长" * 90, "authors": ["作者"]}) == "【作者】" + "长" * 81


def long_single_sync(tmp_path):
    sync, plugin, client, album, photos, calls = api_sync(tmp_path, single=True)
    photos["101"]["name"] = LONG_TITLE
    return sync, plugin, Path(sync.settings()["download_dir"]) / "【ひとにたち】ヒトメボレ"


def test_check_imports_chapter_directory_cut_by_jmcomic_under_another_root(tmp_path):
    async def scenario():
        sync, plugin, album_dir = long_single_sync(tmp_path)
        # JMComic ran with another save root, so its 100-character cut has a different length.
        target = album_dir / f"/volume1/jm/【ひとにたち】ヒトメボレ/第1話 {LONG_TITLE}"[:100].rsplit("/", 1)[1]
        target.mkdir(parents=True)
        for page in (1, 2):
            (target / f"{page:05d}.png").write_bytes(plugin.picture)
        sync.launch(check_local=True)
        await sync.task
        assert sync.current["local_albums"] == 1 and sync.current["local_images"] == 2, sync.current
        sync.launch(["101"])
        await sync.task
        assert sync.current["skipped"] == 2 and not plugin.requests
        assert [p.name for p in album_dir.iterdir()] == [target.name]
    asyncio.run(scenario())


def test_sync_cuts_over_long_chapter_directory_and_check_finds_it(tmp_path):
    async def scenario():
        sync, plugin, album_dir = long_single_sync(tmp_path)
        sync.launch(["101"])
        await sync.task
        assert sync.current["status"] == "completed" and sync.current["downloaded"] == 2, sync.current
        [directory] = album_dir.iterdir()
        assert f"第1話 {LONG_TITLE}".startswith(directory.name) and len(directory.name.encode()) <= 255
        sync.launch(check_local=True)
        await sync.task
        assert sync.current["local_albums"] == 1 and sync.current["local_images"] == 2, sync.current
    asyncio.run(scenario())


def test_two_cut_directories_for_one_chapter_are_ambiguous(tmp_path):
    async def scenario():
        sync, plugin, album_dir = long_single_sync(tmp_path)
        for name in ("第1話 魔物棲息之所", "第1話 魔物棲息之所 時隔6年"):
            (album_dir / name).mkdir(parents=True)
        sync.launch(check_local=True)
        await sync.task
        assert sync.current["local_albums"] == 0
        assert "Pindextitle匹配到多个目录" in sync.current["errors"][0]["message"]
    asyncio.run(scenario())


def test_repair_downloads_over_long_chapter_into_existing_album_directory(tmp_path):
    async def scenario():
        sync, plugin, album_dir = long_single_sync(tmp_path)
        # JMComic created the album directory, then failed on the over-long chapter directory.
        album_dir.mkdir(parents=True)
        sync.launch(check_local=True)
        await sync.task
        assert sync.local_status(["101"])["items"]["101"]["status"] == "error"
        sync.launch(["101"], repair=True)
        await sync.task
        assert sync.current["status"] == "completed" and sync.current["downloaded"] == 2, sync.current
        [directory] = album_dir.iterdir()
        assert f"第1話 {LONG_TITLE}".startswith(directory.name) and len(directory.name.encode()) <= 255
        assert sync.local_status(["101"])["items"]["101"]["status"] == "downloaded"
    asyncio.run(scenario())


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
    assert "【作者】原标题/第1話 章节标题" in jm_client.get("/static/favorite-sync.js").text


def chapter_listing(plugin, chapters):
    original = plugin.detail
    plugin.detail = lambda cid: dict(original(cid), chapters=[{"id": value, "name": name} for value, name in chapters])


@pytest.mark.parametrize("rule", [JM_DIRECTORY_RULE, "title_plain"])
def test_repair_renames_shifted_episode_directory_instead_of_downloading_again(tmp_path, rule):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        sync.save_settings(SyncSettings(**dict(sync.settings(), directory_rule=rule)))
        chapter_listing(plugin, [("1011", "第 1 章"), ("1012", "第 2 章")])
        sync.launch(["101"])
        await sync.task
        album = sync.paths.album(sync.settings(), plugin.detail("101"))
        before = {p.relative_to(album / "第2話 第 2 章"): p.read_bytes() for p in (album / "第2話 第 2 章").iterdir()}
        # Upstream inserts a notice before the second episode, so every later episode number shifts.
        chapter_listing(plugin, [("1011", "第 1 章"), ("1019", "休刊公告"), ("1012", "第 2 章")])
        sync.launch(check_local=True)
        await sync.task
        assert sync.local_status(["101"])["items"]["101"]["status"] == "error"
        downloads = len(plugin.requests)
        sync.launch(["101"], repair=True)
        await sync.task
        state = sync.current
        assert state["status"] == "completed", state
        assert state["renamed_chapters"] == 1 and state["renames"][0]["to"] == "第3話 第 2 章"
        assert state["downloaded"] == 2 and len(plugin.requests) - downloads == 2
        assert sorted(p.name for p in album.iterdir()) == ["第1話 第 1 章", "第2話 休刊公告", "第3話 第 2 章"]
        assert {p.relative_to(album / "第3話 第 2 章"): p.read_bytes() for p in (album / "第3話 第 2 章").iterdir()} == before
        assert sync.local_status(["101"])["items"]["101"]["status"] == "downloaded"
        with sync.store.connect() as db:
            paths = [row[0] for row in db.execute("SELECT path FROM jm_sync_files WHERE chapter_id='1012'")]
        assert paths and all("第3話 第 2 章" in path for path in paths)
    asyncio.run(scenario())


def test_repair_does_not_rename_directory_recorded_for_another_chapter(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        sync.save_settings(SyncSettings(**dict(sync.settings(), directory_rule=JM_DIRECTORY_RULE)))
        chapter_listing(plugin, [("1011", "第 1 章"), ("1018", "休刊公告")])
        sync.launch(["101"])
        await sync.task
        # The old notice was removed upstream and a different notice was added later.
        chapter_listing(plugin, [("1011", "第 1 章"), ("1012", "第 2 章"), ("1019", "休刊公告")])
        sync.launch(check_local=True)
        await sync.task
        sync.launch(["101"], repair=True)
        await sync.task
        state = sync.current
        assert state["status"] == "completed", state
        assert state["renamed_chapters"] == 0 and state["downloaded"] == 4
        album = sync.paths.album(sync.settings(), plugin.detail("101"))
        assert sorted(p.name for p in album.iterdir()) == ["第1話 第 1 章", "第2話 休刊公告", "第2話 第 2 章", "第3話 休刊公告"]
    asyncio.run(scenario())
