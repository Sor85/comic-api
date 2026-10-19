import asyncio
import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from main import create_app
from src.errors import ComicApiError
from src.services.legacy_migration import LegacyMigration
from tests.test_jm_sync import FavoritePlugin, isolated_environment, jm_root, make_sync, save  # noqa: F401


def legacy_album(store, plugin, album_id="101", status="downloaded", chapters=None):
    """Files and records as the removed JM favorites sync left them, in the JMComic-style layout."""
    book = plugin.detail(album_id)
    root = store.root / "jm-favorites"
    album = root / f"【作者】漫画 {album_id}"
    with store.connect() as db:
        for index, chapter in enumerate(book["chapters"][:chapters], 1):
            directory = album / f"第{index}話 {chapter['name']}"
            directory.mkdir(parents=True)
            for page in range(1, plugin.page_count + 1):
                path = directory / f"{page:05d}.png"
                path.write_bytes(plugin.picture)
                db.execute("INSERT INTO jm_sync_files VALUES (?,?,?,?,?,?,?,?)",
                           ("old-scope", album_id, chapter["id"], page, f"/{chapter['id']}/{page}.png", str(path),
                            len(plugin.picture), hashlib.sha256(plugin.picture).hexdigest()))
        db.execute("INSERT INTO jm_local_status VALUES (?,?,?,?,?,?,?,?)",
                   (str(root), album_id, status, book["title"], str(album), 4, "", time.time()))
    return root, album


async def migrate(migration):
    migration.preview()
    await migration.task
    if migration.state["status"] == "ready":
        migration.run()
        await migration.task
    return migration.state


def test_migration_moves_recorded_album_into_library_layout_and_registers_it(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path, books=())
        root, album = legacy_album(library.store, plugin)
        legacy_album(library.store, plugin, "102", status="error")
        (root / "未登记目录").mkdir()
        migration = LegacyMigration(library.store, library.service, library)
        assert migration.pending() == 1
        migration.preview()
        await migration.task
        preview = migration.state
        assert preview["status"] == "ready", preview
        [entry] = preview["albums"]
        assert entry["status"] == "ready" and entry["images"] == 4 and entry["to"] == str(jm_root(library) / "101")
        assert (album / "第1話 第 1 章" / "00001.png").is_file()  # preview never moves files
        assert {name for item in preview["leftovers"] for name in item["entries"]} == {"未登记目录", "【作者】漫画 102"}
        migration.run()
        await migration.task
        state = migration.state
        assert state["status"] == "completed" and state["albums"][0]["status"] == "migrated", state
        assert (jm_root(library) / "101" / "1011" / "00001.png").is_file()
        assert not album.exists() and migration.pending() == 0
        leftovers = {name for item in state["leftovers"] for name in item["entries"]}
        assert leftovers == {"未登记目录", "【作者】漫画 102"}
        book = next(b for b in library.store.books() if b["id"] == "101")
        assert book["library_categories"] == ["JM 收藏"]
        assert library.local_status("jm", ["101"])["items"]["101"]["status"] == "downloaded"
        with library.store.connect() as db:
            assert not db.execute("SELECT * FROM jm_sync_files WHERE album_id='101'").fetchall()
            assert db.execute("SELECT COUNT(*) FROM library_jm_sync_local_files").fetchone()[0] == 4
        # The migrated copy is complete: library sync neither downloads nor reads pages again.
        library.launch(items=[("jm", "101")])
        await library.task
        assert library.current["skipped"] == 4 and not library.current["downloaded"] and not plugin.requests
    asyncio.run(scenario())


def test_partially_downloaded_album_is_completed_by_library_sync(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path, books=())
        legacy_album(library.store, plugin, chapters=1)
        migration = LegacyMigration(library.store, library.service, library)
        state = await migrate(migration)
        assert state["albums"][0]["status"] == "migrated" and "部分章节" in state["albums"][0]["message"]
        assert "101" not in library.local_status("jm", ["101"])["items"]
        library.launch(items=[("jm", "101")])
        await library.task
        assert library.current["skipped"] == 2 and library.current["downloaded"] == 2
    asyncio.run(scenario())


def test_occupied_target_is_reported_and_nothing_moves(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path, books=())
        _, album = legacy_album(library.store, plugin)
        occupied = jm_root(library) / "101" / "1011" / "00001.png"
        occupied.parent.mkdir(parents=True)
        occupied.write_bytes(b"user file")
        migration = LegacyMigration(library.store, library.service, library)
        state = await migrate(migration)
        assert state["status"] == "completed" and state["albums"][0]["status"] == "failed"
        assert "已有其他文件" in state["albums"][0]["message"]
        assert occupied.read_bytes() == b"user file" and (album / "第1話 第 1 章" / "00001.png").is_file()
        assert migration.pending() == 1
    asyncio.run(scenario())


def test_album_already_complete_in_library_keeps_old_files(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path, books=("101",))
        library.launch()
        await library.task
        _, album = legacy_album(library.store, plugin)
        migration = LegacyMigration(library.store, library.service, library)
        state = await migrate(migration)
        assert state["albums"][0]["status"] == "skipped" and album.is_dir()
    asyncio.run(scenario())


def test_failed_move_puts_album_back(tmp_path, monkeypatch):
    async def scenario():
        library, plugin = make_sync(tmp_path, books=())
        _, album = legacy_album(library.store, plugin)
        migration = LegacyMigration(library.store, library.service, library)
        migration.preview()
        await migration.task
        import shutil
        original, calls = shutil.move, []

        def flaky(source, target):
            calls.append(source)
            if len(calls) == 3:
                raise OSError("disk full")
            return original(source, target)
        monkeypatch.setattr("src.services.legacy_migration.shutil.move", flaky)
        migration.run()
        await migration.task
        assert migration.state["albums"][0]["status"] == "failed"
        assert sorted(p.name for p in (album / "第1話 第 1 章").iterdir()) == ["00001.png", "00002.png"]
        assert not any(jm_root(library).rglob("*.png")) and migration.pending() == 1
    asyncio.run(scenario())


def test_run_requires_fresh_preview_and_blocks_library_sync(tmp_path):
    async def scenario():
        library, plugin = make_sync(tmp_path, books=())
        legacy_album(library.store, plugin)
        migration = LegacyMigration(library.store, library.service, library)
        with pytest.raises(ComicApiError) as error:
            migration.run()
        assert error.value.code == "preview_required"
        migration.preview()
        await migration.task
        save(library, directory_rule="title")
        with pytest.raises(ComicApiError) as error:
            migration.run()
        assert error.value.code == "preview_stale"
        migration.preview()
        await migration.task
        migration.run()
        with pytest.raises(ComicApiError) as error:
            library.launch()
        assert error.value.code == "migration_active"
        await migration.task
        assert library.blocked_by is None and migration.state["albums"][0]["status"] == "migrated"
        assert (jm_root(library) / "漫画 101 [101]" / "001 - 第 1 章 [1011]" / "00001.png").is_file()
    asyncio.run(scenario())


def test_migration_api(tmp_path):
    library, plugin = make_sync(tmp_path, books=())
    legacy_album(library.store, plugin)
    with TestClient(create_app(library.store, {"jm": plugin})) as client:
        assert client.get("/api/library/migration").json()["pending"] == 1
        assert client.post("/api/library/migration/run").status_code == 409
        assert client.post("/api/library/migration/preview").status_code == 202
        for _ in range(300):
            data = client.get("/api/library/migration").json()
            if not data["active"]:
                break
            time.sleep(.01)
        assert data["status"] == "ready"
        assert client.post("/api/library/migration/run").status_code == 202
        for _ in range(300):
            data = client.get("/api/library/migration").json()
            if not data["active"]:
                break
            time.sleep(.01)
        assert data["status"] == "completed" and data["pending"] == 0
        html = client.get("/").text
        assert 'id="legacy-migration-dialog"' in html and 'id="legacy-migration-open"' in html
