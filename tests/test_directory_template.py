import asyncio
import hashlib
import json

import pytest

from src.errors import ComicApiError
from src.services.image_sync import ImageSync, ImageSyncSettings
from src.services.library_migration import LibraryMigration
from src.services.local_images import DEFAULT_TEMPLATE, parse_template
from tests.test_jm_sync import FavoritePlugin, isolated_environment, jm_root, make_sync, save  # noqa: F401
from tests.test_library_migration import entry, migrate


def custom(library, template):
    save(library, directory_rule="custom", directory_template=template)


@pytest.mark.parametrize("template, message", [
    ("", "不能为空"),
    ("{title} [{id}]", "至少需要两层"),
    ("{title}/{nope}", "未知变量 {nope}"),
    ("{author}/{index}", "需包含 {id} 或 {title}"),
    ("{title}/{chapter}", "需包含 {chapter_id} 或 {index}"),
    ("{title}/{index}/{chapter}", "只能用于最后一层"),
    ("{title}/{chapter_id:03}", "仅 {index} 支持"),
    ("{title}/{index:3}", "仅 {index} 支持"),
    ("{title/{index}", "花括号不成对"),
    ("{title}?/{index}", "文件名禁用字符"),
    ("{title}/../{index}", "空层级"),
    ("{title}//{index}", "空层级"),
    ("/".join(["{title}"] * 8 + ["{index}"]), "最多 8 层"),
])
def test_invalid_templates_name_the_problem(template, message):
    with pytest.raises(ValueError, match=message.replace("{", r"\{").replace("}", r"\}").replace("?", r"\?")):
        parse_template(template)


def test_template_matching_built_in_rule_renders_identical_paths(tmp_path):
    library, plugin = make_sync(tmp_path)
    worker = library.worker("jm")
    book = dict(plugin.detail("101"), title="../../漫画\\标题", author="CON")
    chapter = {"id": "1011", "name": "../../章节"}
    built_in = dict(worker.settings(), directory_rule="author")
    template = dict(worker.settings(), directory_rule="custom", directory_template=DEFAULT_TEMPLATE)
    assert worker.paths.chapter(template, book, chapter, 1) == worker.paths.chapter(built_in, book, chapter, 1)
    assert parse_template("\\{title}\\{index}\\") == parse_template("{title}/{index}")


def test_template_fields_render_category_source_and_padding(tmp_path):
    library, plugin = make_sync(tmp_path)
    worker = library.worker("jm")
    config = dict(worker.settings(), directory_rule="custom",
                  directory_template="{category}/{source}-{id}/第{index:02}话 {chapter}")
    book = plugin.detail("101")
    path = worker.paths.chapter(config, book, book["chapters"][0], 3)
    assert path.relative_to(config["download_dir"]).parts == ("未分类", "jm-101", "第03话 第 1 章")
    library.store.set_category("jm", "101", "精选")
    assert worker.paths.album(config, book).parent.name == "精选"


def test_settings_api_validates_only_the_custom_rule(tmp_path):
    library, _ = make_sync(tmp_path)
    with pytest.raises(ComicApiError, match="需包含 {id} 或 {title}") as error:
        custom(library, "{author}/{index}")
    assert error.value.status_code == 422
    with pytest.raises(ComicApiError, match="不能为空"):
        custom(library, " / ")
    # A built-in rule ignores the unused template, as the hidden field still sends it.
    save(library, directory_rule="title", directory_template="{author}/{index}")
    custom(library, " {title} [{id}]\\{index:03} ")
    assert library.settings()["directory_template"] == "{title} [{id}]/{index:03}"
    assert {"value": "custom", "label": "自定义模板"} in library.options()["directory_rules"]
    assert library.options()["default_template"] == DEFAULT_TEMPLATE


def test_built_in_rules_keep_their_checkpoint_scope(tmp_path):
    library, _ = make_sync(tmp_path)
    config = library.settings()
    before = hashlib.sha256(json.dumps([config[key] for key in
                                       ("download_dir", "directory_rule", "image_format", "quality")]).encode()).hexdigest()
    assert ImageSync.output_scope(config) == before
    first = ImageSync.output_scope(dict(config, directory_rule="custom", directory_template="{id}/{index}"))
    assert first != ImageSync.output_scope(dict(config, directory_rule="custom", directory_template="{id}/{chapter_id}"))


def test_custom_template_sync_writes_and_skips(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        custom(library, "{author}/{title} ({id})/{index:02} {chapter}")
        library.launch()
        await library.task
        assert library.current["status"] == "completed", library.current
        page = jm_root(library) / "作者 _ name" / "漫画 101 (101)" / "01 第 1 章" / "00001.png"
        assert page.is_file()
        assert library.local_status("jm", ["101"])["items"]["101"]["path"] == str(page.parents[1])
        plugin.requests.clear()
        library.launch()
        await library.task
        assert library.current["skipped"] == 8 and not plugin.requests
    asyncio.run(scenario())


def test_check_matches_template_by_id_after_title_change(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        custom(library, "{category}/{title} ({id})/{chapter_id}")
        album = jm_root(library) / "未分类" / "旧标题 (101)"
        for chapter in plugin.detail("101")["chapters"]:
            (album / chapter["id"]).mkdir(parents=True)
            for page in (1, 2):
                (album / chapter["id"] / f"{page}.png").write_bytes(plugin.picture)
        library.launch(mode="check_local")
        await library.task
        item = library.local_status("jm", ["101"])["items"]["101"]
        assert item["status"] == "downloaded" and item["path"] == str(album)
    asyncio.run(scenario())


def test_template_without_id_guards_same_title_directories(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        custom(library, "{title}/{index}")
        library.launch(items=[("jm", "101")])
        await library.task
        plugin.detail = lambda comic_id, detail=plugin.detail: dict(detail(comic_id), title="漫画 101")
        library.launch(items=[("jm", "102")])
        await library.task
        assert library.current["failed"] == 1 and "未混合写入" in library.current["errors"][0]["message"]
    asyncio.run(scenario())


def test_migration_relays_out_custom_layout_after_switching_rules(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        custom(library, "{author}/{title}-{id}/{chapter_id}")
        library.launch()
        await library.task
        old = jm_root(library) / "作者 _ name"
        assert (old / "漫画 101-101" / "1011" / "00001.png").is_file()
        save(library, directory_rule="title")
        migration = LibraryMigration(library.store, library.service, library)
        state = await migrate(migration, library.settings()["download_dir"])
        assert {item["status"] for item in state["albums"]} == {"migrated"}, state
        assert (jm_root(library) / "漫画 101 [101]" / "001 - 第 1 章 [1011]" / "00001.png").is_file()
        assert not old.exists()
    asyncio.run(scenario())


def test_migration_uses_recorded_directory_of_a_replaced_template(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        custom(library, "收藏/{title}/{chapter_id}")
        library.launch()
        await library.task
        custom(library, "{id}/{index:03}")
        migration = LibraryMigration(library.store, library.service, library)
        state = await migrate(migration, library.settings()["download_dir"])
        assert entry(state)["status"] == "migrated", state
        assert (jm_root(library) / "101" / "001" / "00001.png").is_file()
        assert not (jm_root(library) / "收藏").exists()
        plugin.requests.clear()
        library.launch()
        await library.task
        assert library.current["skipped"] == 8 and not library.current["downloaded"]
    asyncio.run(scenario())
