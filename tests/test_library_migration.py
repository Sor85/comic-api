import asyncio
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from main import create_app
from src.errors import ComicApiError
from src.services.library_migration import LibraryMigration, original_name
from tests.test_jm_sync import FavoritePlugin, isolated_environment, jm_root, make_sync, save  # noqa: F401


def write_album(plugin, album, album_id="101", chapters=None, name="第{index}話 {chapter}"):
    """Pages of a comic in an arbitrary layout: ``album`` / chapter directory named by ``name`` / 00001.png."""
    book = plugin.detail(album_id)
    for index, chapter in enumerate(book["chapters"][:chapters], 1):
        directory = album / name.format(index=index, chapter=chapter["name"], id=chapter["id"])
        directory.mkdir(parents=True)
        for page in range(1, plugin.page_count + 1):
            (directory / f"{page:05d}.png").write_bytes(plugin.picture)
    return album


async def preview(migration, source_dir):
    migration.preview(str(source_dir))
    await migration.task
    return migration.state


async def migrate(migration, source_dir):
    await preview(migration, source_dir)
    if migration.state["status"] == "ready":
        migration.run()
        await migration.task
    return migration.state


def entry(state, album_id="101"):
    return next(item for item in state["albums"] if item["id"] == album_id)


def test_original_name_takes_first_token_outside_brackets():
    assert original_name("[作者] 标题 (C99) [中国翻訳]") == "标题"
    assert original_name("漫画 101") == "漫画 101"
    assert original_name("【只有括号】") == "【只有括号】"


def test_jmcomic_layout_is_moved_into_current_rule_and_registered(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        source = library.store.root / "jm-favorites"
        album = write_album(plugin, source / "【作者】漫画 101")
        (source / "未识别目录").mkdir()
        migration = LibraryMigration(library.store, library.service, library)
        assert str(source.resolve()) in migration.status()["roots"]
        state = await preview(migration, source)
        assert state["status"] == "ready", state
        assert len(state["albums"]) == 1  # 102 has no directory and is not listed
        assert entry(state)["status"] == "ready" and entry(state)["images"] == 4
        assert entry(state)["to"] == str(jm_root(library) / "101")
        assert (album / "第1話 第 1 章" / "00001.png").is_file()  # preview never moves files
        assert [name for item in state["leftovers"] for name in item["entries"]] == ["未识别目录"]
        plugin.requests.clear()
        migration.run()
        await migration.task
        state = migration.state
        assert state["status"] == "completed" and entry(state)["status"] == "migrated", state
        assert (jm_root(library) / "101" / "1011" / "00001.png").is_file()
        assert not album.exists()
        assert [name for item in state["leftovers"] for name in item["entries"]] == ["未识别目录"]
        assert library.local_status("jm", ["101"])["items"]["101"]["status"] == "downloaded"
        # The migrated copy is complete: library sync neither downloads nor reads pages again.
        library.launch(items=[("jm", "101")])
        await library.task
        assert library.current["skipped"] == 4 and not library.current["downloaded"] and not plugin.requests
    asyncio.run(scenario())


def test_library_directory_is_relaid_out_in_place_after_rule_change(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path, rule="title")
        library.launch()
        await library.task
        old = jm_root(library) / "漫画 101 [101]"
        assert (old / "001 - 第 1 章 [1011]" / "00001.png").is_file()
        save(library, directory_rule="author")
        migration = LibraryMigration(library.store, library.service, library)
        state = await migrate(migration, library.settings()["download_dir"])
        assert state["status"] == "completed" and {item["status"] for item in state["albums"]} == {"migrated"}, state
        target = jm_root(library) / "作者 _ name" / "漫画 101 [101]" / "001 - 第 1 章 [1011]" / "00001.png"
        assert target.is_file() and not old.exists()
        assert not state["leftovers"]
        with library.store.connect() as db:
            assert not db.execute("SELECT * FROM library_jm_sync_files").fetchall()
        plugin.requests.clear()
        library.launch()
        await library.task
        assert library.current["skipped"] == 8 and not library.current["downloaded"] and not plugin.requests
    asyncio.run(scenario())


def test_any_layout_with_ids_matches_by_id(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        source = library.store.root / "library" / "imports"
        write_album(plugin, source / "随便起的名字 [101]", name="{index:03d} - 任意 [{id}]")
        migration = LibraryMigration(library.store, library.service, library)
        state = await migrate(migration, source)
        assert entry(state)["status"] == "migrated", state
        assert (jm_root(library) / "101" / "1012" / "00002.png").is_file()
    asyncio.run(scenario())


def test_partial_copy_is_completed_by_library_sync(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        write_album(plugin, library.store.root / "jm-favorites" / "漫画 101", chapters=1)
        migration = LibraryMigration(library.store, library.service, library)
        state = await migrate(migration, library.store.root / "jm-favorites")
        assert entry(state)["status"] == "migrated" and "缺少 1 章" in entry(state)["message"]
        assert library.local_status("jm", ["101"])["items"]["101"]["status"] == "error"
        library.launch(items=[("jm", "101")])
        await library.task
        assert library.current["skipped"] == 2 and library.current["downloaded"] == 2
        assert library.local_status("jm", ["101"])["items"]["101"]["status"] == "downloaded"
    asyncio.run(scenario())


def test_occupied_target_is_reported_and_nothing_moves(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        album = write_album(plugin, library.store.root / "jm-favorites" / "漫画 101")
        occupied = jm_root(library) / "101" / "1011" / "00001.png"
        occupied.parent.mkdir(parents=True)
        occupied.write_bytes(b"user file")
        migration = LibraryMigration(library.store, library.service, library)
        state = await migrate(migration, album.parent)
        assert state["status"] == "completed" and entry(state)["status"] == "failed"
        assert "已有其他文件" in entry(state)["message"]
        assert occupied.read_bytes() == b"user file" and (album / "第1話 第 1 章" / "00001.png").is_file()
    asyncio.run(scenario())


def test_same_id_in_two_directories_is_not_guessed(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        source = library.store.root / "jm-favorites"
        write_album(plugin, source / "101")
        write_album(plugin, source / "漫画 101 [101]")
        migration = LibraryMigration(library.store, library.service, library)
        state = await preview(migration, source)
        assert state["status"] == "completed" and entry(state)["status"] == "failed"
        assert "多个目录" in entry(state)["message"]
    asyncio.run(scenario())


def test_album_already_complete_in_library_keeps_source_files(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        library.launch()
        await library.task
        album = write_album(plugin, library.store.root / "jm-favorites" / "漫画 101")
        migration = LibraryMigration(library.store, library.service, library)
        state = await migrate(migration, album.parent)
        assert entry(state)["status"] == "skipped" and album.is_dir()
    asyncio.run(scenario())


def test_failed_move_puts_album_back(tmp_path, monkeypatch):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        album = write_album(plugin, library.store.root / "jm-favorites" / "漫画 101")
        migration = LibraryMigration(library.store, library.service, library)
        await preview(migration, album.parent)
        original, calls = shutil.move, []

        def flaky(source, target):
            calls.append(source)
            if len(calls) == 3:
                raise OSError("disk full")
            return original(source, target)
        monkeypatch.setattr("src.services.library_migration.shutil.move", flaky)
        migration.run()
        await migration.task
        assert entry(migration.state)["status"] == "failed"
        assert sorted(p.name for p in (album / "第1話 第 1 章").iterdir()) == ["00001.png", "00002.png"]
        assert not any(jm_root(library).rglob("*.png"))
    asyncio.run(scenario())


def test_run_requires_fresh_preview_and_blocks_library_sync(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        source = library.store.root / "jm-favorites"
        write_album(plugin, source / "漫画 101")
        migration = LibraryMigration(library.store, library.service, library)
        with pytest.raises(ComicApiError) as error:
            migration.run()
        assert error.value.code == "preview_required"
        with pytest.raises(ComicApiError) as error:
            migration.preview("/etc")
        assert error.value.code == "invalid_directory"
        await preview(migration, source)
        save(library, directory_rule="title")
        with pytest.raises(ComicApiError) as error:
            migration.run()
        assert error.value.code == "preview_stale"
        await preview(migration, source)
        migration.run()
        with pytest.raises(ComicApiError) as error:
            library.launch()
        assert error.value.code == "migration_active"
        await migration.task
        assert library.blocked_by is None and entry(migration.state)["status"] == "migrated"
        assert (jm_root(library) / "漫画 101 [101]" / "001 - 第 1 章 [1011]" / "00001.png").is_file()
    asyncio.run(scenario())


def wait_idle(client):
    for _ in range(300):
        data = client.get("/api/library/migration").json()
        if not data["active"]:
            return data
        time.sleep(.01)
    raise AssertionError("migration did not finish")


def test_migration_api(tmp_path):
    library, plugin = make_sync(tmp_path)
    source = library.store.root / "jm-favorites"
    write_album(plugin, source / "漫画 101")
    with TestClient(create_app(library.store, {"jm": plugin})) as client:
        status = client.get("/api/library/migration").json()
        assert status["status"] == "idle" and status["target"]["directory_rule"] == "id"
        listing = client.get("/api/library/migration/directories", params={"path": str(source)}).json()
        assert [item["name"] for item in listing["directories"]] == ["漫画 101"]
        assert client.post("/api/library/migration/run").status_code == 409
        assert client.post("/api/library/migration/preview", json={}).status_code == 422
        assert client.post("/api/library/migration/preview", json={"source_dir": str(source)}).status_code == 202
        assert wait_idle(client)["status"] == "ready"
        assert client.post("/api/library/migration/run").status_code == 202
        data = wait_idle(client)
        assert data["status"] == "completed" and data["albums"][0]["status"] == "migrated"
        html = client.get("/").text
        assert 'id="library-migration-dialog"' in html and 'id="library-migration-open"' in html


def test_single_chapter_pages_directly_in_comic_directory(tmp_path, monkeypatch):
    async def scenario():
        library, plugin = make_sync(tmp_path)
        detail = plugin.detail
        monkeypatch.setattr(plugin, "detail", lambda comic_id: dict(detail(comic_id), chapters=detail(comic_id)["chapters"][:1]))
        album = library.store.root / "jm-favorites" / "漫画 101"
        album.mkdir(parents=True)
        for page in (1, 2):
            (album / f"{page}.png").write_bytes(plugin.picture)
        migration = LibraryMigration(library.store, library.service, library)
        state = await migrate(migration, album.parent)
        assert entry(state)["status"] == "migrated" and not entry(state)["message"], state
        assert sorted(p.name for p in (jm_root(library) / "101" / "1011").iterdir()) == ["00001.png", "00002.png"]
        assert library.local_status("jm", ["101"])["items"]["101"]["status"] == "downloaded"
    asyncio.run(scenario())
