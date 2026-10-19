import asyncio
from pathlib import Path

import pytest

from src.errors import ComicApiError
from src.services.local_images import plain_chapter_name, safe_name
from tests.test_jm_sync import isolated_environment, jm_client, jm_root, make_sync, save


def plain_sync(tmp_path):
    return make_sync(tmp_path, rule="title_plain")


def chapter_path(sync, book, chapter, index, **values):
    worker = sync.worker("jm")
    return worker.paths.chapter(dict(worker.settings(), **values), book, chapter, index)


def only_101(sync, plugin):
    """Limit full runs to comic 101 through a library category."""
    sync.store.save_book("jm", "101", plugin.detail("101"), "仅此")
    save(sync, categories=["仅此"])


@pytest.mark.parametrize("index", [1, 2, 12])
def test_plain_rule_matches_title_episode_layout_without_ids(tmp_path, index):
    sync, plugin = plain_sync(tmp_path)
    book = dict(plugin.detail("101"), title="【ひとにたち】ヒトメボレ")
    chapter = {"id": "1011", "name": "[漢化] [ひとにたち] ヒトメボレ"}
    target = chapter_path(sync, book, chapter, index)
    assert target.relative_to(jm_root(sync)).parts == (
        book["title"], f"第{index}話 {chapter['name']}")
    assert "101" not in target.name and "001 -" not in target.name


@pytest.mark.parametrize("title, expected", [
    ("章节标题", "第1話 章节标题"),
    ("第1話 章节标题", "第1話 章节标题"),
    ("第 01 话 章节标题", "第1話 章节标题"),
    ("第1話", "第1話"),
    ("第2話 章节标题", "第1話 第2話 章节标题"),
])
def test_plain_rule_does_not_duplicate_existing_episode_prefix(title, expected):
    assert plain_chapter_name({"name": title}, 1) == expected


def test_plain_rule_preserves_long_titles_and_existing_rules_stay_unchanged(tmp_path):
    sync, plugin = plain_sync(tmp_path)
    title = "完整漫画标题" * 9
    chapter_title = "完整章节标题" * 9
    assert 100 < len(title.encode()) < 255
    book = dict(plugin.detail("101"), title=title)
    chapter = {"id": "1011", "name": chapter_title}
    target = chapter_path(sync, book, chapter, 1)
    assert target.parts[-2:] == (title, f"第1話 {chapter_title}")
    assert len(safe_name(title).encode()) <= 100
    previous = chapter_path(sync, book, chapter, 1, directory_rule="title")
    assert previous.parts[-2:] == (f"{safe_name(title)} [101]", f"001 - {safe_name(chapter_title)} [1011]")


@pytest.mark.parametrize("field, expected", [("album", "长" * 85), ("chapter", "第1話 " + "长" * 82)])
def test_plain_rule_cuts_oversized_names_to_filesystem_limit(tmp_path, field, expected):
    sync, plugin = plain_sync(tmp_path)
    book = plugin.detail("101")
    chapter = dict(book["chapters"][0])
    if field == "album":
        book["title"] = "长" * 86
    else:
        chapter["name"] = "长" * 84
    target = chapter_path(sync, book, chapter, 1)
    assert (target.parent.name if field == "album" else target.name) == expected
    assert not jm_root(sync).exists()


def test_plain_rule_keeps_path_boundaries_and_windows_sanitization(tmp_path):
    sync, plugin = plain_sync(tmp_path)
    book = dict(plugin.detail("101"), title="CON")
    chapter = {"id": "1011", "name": "../../章节\\名称:副题"}
    target = chapter_path(sync, book, chapter, 1)
    assert target.relative_to(jm_root(sync)).parts == (
        "_CON", "第1話 .._.._章节_名称_副题")
    root = jm_root(sync)
    root.mkdir(parents=True)
    (root / "_CON").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ComicApiError, match="符号链接"):
        chapter_path(sync, book, chapter, 1)


def test_plain_downloads_can_be_checked_imported_and_reused(tmp_path):
    async def scenario():
        sync, plugin = plain_sync(tmp_path)
        original = plugin.detail
        def detail(album_id):
            book = original(album_id)
            if album_id == "101":
                book["title"] = "保留完整标题" * 9 + ":外传"
                for index, chapter in enumerate(book["chapters"], 1):
                    chapter["name"] = f"第{index}话 " + "完整章节标题" * 8 + ":后记"
            return book
        plugin.detail = detail
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["status"] == "completed" and sync.current["downloaded"] == 4
        book = plugin.detail("101")
        target = chapter_path(sync, book, book["chapters"][0], 1)
        assert (target / "00001.png").is_file()
        assert target.parent.name == book["title"].replace(":", "_")
        assert target.name == "第1話 " + ("完整章节标题" * 8) + "_后记"
        before = len(plugin.requests)
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["skipped"] == 4 and len(plugin.requests) == before
        # Only the test database loses its index; the existing images stay in place.
        with sync.store.connect() as db:
            db.execute("DELETE FROM library_jm_sync_files")
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["local_albums"] == 1 and not sync.current["failed"], sync.current
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["skipped"] == 4 and len(plugin.requests) == before
    asyncio.run(scenario())


@pytest.mark.parametrize("album_title, chapter_title", [("102", "标题 [999]"), ("标题 [102]", "1234")])
def test_plain_name_digits_are_not_mistaken_for_appended_ids(tmp_path, album_title, chapter_title):
    async def scenario():
        sync, plugin = plain_sync(tmp_path)
        original = plugin.detail
        def detail(album_id):
            book = original(album_id)
            if album_id == "101":
                book["title"] = album_title
                for chapter in book["chapters"]:
                    chapter["name"] = chapter_title
            return book
        plugin.detail = detail
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["status"] == "completed"
        # Restrict the check to 101; its literal title intentionally resembles another ID.
        only_101(sync, plugin)
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["local_albums"] == 1 and not sync.current["failed"], sync.current
    asyncio.run(scenario())


@pytest.mark.parametrize("imported", [False, True])
def test_plain_rule_refuses_mixing_different_comics_in_registered_directory(tmp_path, imported):
    async def scenario():
        sync, plugin = plain_sync(tmp_path)
        original = plugin.detail
        def detail(album_id):
            book = dict(original(album_id), title="同名漫画")
            for chapter in book["chapters"]:
                chapter["name"] = f"{album_id} 的 {chapter['name']}"
            return book
        plugin.detail = detail
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["downloaded"] == 4
        if imported:
            with sync.store.connect() as db:
                db.execute("DELETE FROM library_jm_sync_files")
            only_101(sync, plugin)
            sync.launch(mode="check_local")
            await sync.task
            assert sync.current["local_albums"] == 1
        root = jm_root(sync)
        before = {p: p.read_bytes() for p in root.rglob("*.png")}
        request_count = len(plugin.requests)
        sync.launch(items=[("jm", "102")])
        await sync.task
        assert sync.current["failed"] == 1 and "未混合写入" in sync.current["errors"][0]["message"]
        assert len(plugin.requests) == request_count
        assert {p: p.read_bytes() for p in root.rglob("*.png")} == before
    asyncio.run(scenario())


def test_directory_rule_labels_match_between_ui_and_api(jm_client):
    rules = jm_client.get("/api/library/sync/settings").json()["directory_rules"]
    assert [rule["label"] for rule in rules] == [
        "漫画 ID / 章节 ID",
        "漫画标题 [ID] / 章节序号 - 章节标题 [ID]",
        "漫画标题 / 第N話 章节标题",
        "作者 / 漫画标题 [ID] / 章节序号 - 章节标题 [ID]",
        "自定义模板",
    ]
    html = jm_client.get("/").text
    select = html.split('<select name="directory_rule">', 1)[1].split("</select>", 1)[0]
    assert select.count("<option ") == len(rules)
    for rule in rules:
        assert f'<option value="{rule["value"]}">{rule["label"]}</option>' in select
    assert "<strong>无 ID 规则</strong>" in html and "Bd_Aauthoroname_Pindextitle" not in html


def test_plain_rule_setting_and_ui_option(jm_client):
    options = jm_client.get("/api/library/sync/settings").json()
    assert any(rule["value"] == "title_plain" for rule in options["directory_rules"])
    settings = dict(options["settings"], directory_rule="title_plain")
    assert jm_client.put("/api/library/sync/settings", json=settings).status_code == 200
    assert jm_client.get("/api/library/sync/settings").json()["settings"]["directory_rule"] == "title_plain"
    assert 'value="title_plain">漫画标题 / 第N話 章节标题</option>' in jm_client.get("/").text
    assert "漫画标题/第1話 章节标题" in jm_client.get("/static/library-sync.js").text


def test_plain_rule_check_finds_directories_cut_by_sync(tmp_path):
    async def scenario():
        sync, plugin = plain_sync(tmp_path)
        original = plugin.detail
        long = {"title": "很长的标题" * 20, "chapters": [{"id": "1011", "name": "很长的章节名" * 20}]}
        plugin.detail = lambda cid: dict(original(cid), **(long if cid == "101" else {}))
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["status"] == "completed" and sync.current["downloaded"] == 2, sync.current
        [album] = jm_root(sync).iterdir()
        [chapter] = album.iterdir()
        assert len(album.name.encode()) <= 255 and len(chapter.name.encode()) <= 255
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["local_albums"] == 1 and sync.current["local_images"] == 2, sync.current
    asyncio.run(scenario())
