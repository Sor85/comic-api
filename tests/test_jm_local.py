import asyncio
import io
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.errors import ComicApiError
from src.services.favorites import JmFavorites
from src.services.image_sync import ImageSyncSettings
from src.services.library_image_sync import LibraryImageSync
from src.services.local_check import LocalLibrary
from src.services.local_images import safe_name
from src.storage import Store
from tests.test_jm_sync import isolated_environment, jm_root, make_sync, save, wait_for_run


def existing_album(sync, plugin, album_id="101", layout="plain"):
    book = plugin.detail(album_id)
    root = jm_root(sync)
    if layout in ("plain", "prefixed"):
        album = root / book["title"]
    elif layout == "id":
        album = root / album_id
    else:
        album = root / f"{safe_name(book['title'])} [{album_id}]"
        if layout == "author":
            album = root / safe_name(book["author"]) / album.name
    for index, chapter in enumerate(book["chapters"], 1):
        name = (chapter["name"] if layout == "plain" else f"第{index}話 {chapter['name']}" if layout == "prefixed"
                else chapter["id"] if layout == "id" else f"{index:03d} - {safe_name(chapter['name'])} [{chapter['id']}]")
        target = album / name
        target.mkdir(parents=True)
        for page in range(1, plugin.page_count + 1):
            (target / f"{page:05d}.png").write_bytes(plugin.picture)
    return album


def imported(sync):
    with sync.store.connect() as db:
        return db.execute("SELECT * FROM library_jm_sync_local_files ORDER BY album_id, chapter_id, page").fetchall()


def snapshot(directory):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.rglob("*") if p.is_file()}


@pytest.mark.parametrize("layout", ["id", "title", "author", "plain", "prefixed"])
def test_existing_favorites_are_verified_without_downloading_or_modifying(tmp_path, layout):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        directory = existing_album(sync, plugin, layout=layout)
        before = snapshot(directory)
        sync.next_run_at = time.time() + 600
        next_run = sync.next_run_at
        sync.launch(mode="check_local")
        await sync.task
        state = sync.current
        assert state["status"] == "completed", state
        assert state["mode"] == "check_local" and state["local_albums"] == 1
        assert state["local_images"] == 4 and state["local_missing"] == 1
        assert state["albums_done"] == state["albums_total"] == 2
        assert not state["downloaded"] and not state["received_bytes"] and not plugin.requests
        assert len(imported(sync)) == 4
        assert snapshot(directory) == before and sync.next_run_at == next_run
        # A restart and changed output settings do not transcode imported, user-owned images.
        restarted = LibraryImageSync(Store(tmp_path), sync.service, {})
        restarted.save_settings(ImageSyncSettings(**dict(sync.settings(), directory_rule="author", image_format="webp", quality=75)))
        restarted.launch(items=[("jm", "101")])
        await restarted.task
        assert restarted.current["skipped"] == 4 and not plugin.requests
        assert snapshot(jm_root(sync)) == before
    asyncio.run(scenario())


def test_check_is_scoped_to_categories_and_preserves_library_category(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        first = existing_album(sync, plugin)
        second = existing_album(sync, plugin, "102")
        sync.store.save_book("jm", "101", plugin.detail("101"), "已有分类")
        save(sync, categories=["已有分类"])
        before = snapshot(second)
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["albums_total"] == sync.current["local_albums"] == 1
        assert {row["album_id"] for row in imported(sync)} == {"101"}
        assert snapshot(second) == before and first.is_dir()
        assert next(b for b in sync.store.books() if b["id"] == "101")["library_category"] == "已有分类"
        assert not plugin.requests
    asyncio.run(scenario())


@pytest.mark.parametrize("damage", ["empty", "corrupt", "truncated", "missing_page", "extra_page", "gap", "duplicate_page", "missing_chapter", "symlink", "changed_valid_image"])
def test_incomplete_or_damaged_albums_are_not_imported(tmp_path, damage):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        directory = existing_album(sync, plugin)
        target = directory / "第 2 章" / "00002.png"
        if damage == "empty":
            target.write_bytes(b"")
        elif damage == "corrupt":
            target.write_bytes(b"not an image")
        elif damage == "truncated":
            target.write_bytes(plugin.picture[:len(plugin.picture)//2])
        elif damage == "missing_page":
            target.unlink()
        elif damage == "extra_page":
            target.with_name("00003.png").write_bytes(plugin.picture)
        elif damage == "gap":
            target.rename(target.with_name("00003.png"))
        elif damage == "duplicate_page":
            target.rename(target.with_name("1.png"))
        elif damage == "missing_chapter":
            target.parent.rename(target.parent.with_name("unrelated"))
        elif damage == "symlink":
            external = tmp_path / "external.png"
            external.write_bytes(plugin.picture)
            target.unlink()
            target.symlink_to(external)
        else:
            # A valid but changed image must not silently replace an established baseline.
            sync.launch(mode="check_local")
            await sync.task
            target.write_bytes(plugin.picture + b"changed")
        before = snapshot(directory)
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["status"] == "partial", sync.current
        assert sync.current["failed"] == 1 and sync.current["local_albums"] == 0
        assert len(imported(sync)) == (4 if damage == "changed_valid_image" else 0)
        assert snapshot(directory) == before and not plugin.requests
    asyncio.run(scenario())


def test_image_load_detects_truncated_jpeg_that_passes_verify(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        output = io.BytesIO()
        Image.effect_noise((100, 100), 50).convert("RGB").save(output, "JPEG")
        plugin.picture = output.getvalue()[:-100]
        with Image.open(io.BytesIO(plugin.picture)) as image:
            image.verify()
        existing_album(sync, plugin)
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["failed"] == 1 and not imported(sync)
    asyncio.run(scenario())


def test_duplicate_album_names_require_ids(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        original = plugin.detail
        plugin.detail = lambda album_id: dict(original(album_id), title="同名漫画")
        for album_id in ("101", "102"):
            sync.store.save_book("jm", album_id, plugin.detail(album_id))
        existing_album(sync, plugin)
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["failed"] == 2 and not imported(sync)
        existing_album(sync, plugin, layout="title")
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["local_albums"] == 1 and sync.current["failed"] == 1
        assert {r["album_id"] for r in imported(sync)} == {"101"}
    asyncio.run(scenario())


def test_empty_remote_titles_do_not_match_fallback_directory_names(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        original = plugin.detail
        plugin.detail = lambda album_id: dict(original(album_id), title="未命名")
        existing_album(sync, plugin)
        plugin.detail = lambda album_id: dict(original(album_id), title="")
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["failed"] == 2 and not sync.current["local_albums"] and not imported(sync)
    asyncio.run(scenario())


def test_multiple_id_directories_are_ambiguous(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        existing_album(sync, plugin, layout="id")
        existing_album(sync, plugin, layout="title")
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["failed"] == 1 and not imported(sync)
    asyncio.run(scenario())


def test_multiple_chapters_cannot_reuse_one_directory(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        existing_album(sync, plugin)
        original = plugin.detail
        def detail(album_id):
            book = original(album_id)
            book["chapters"][1]["name"] = book["chapters"][0]["name"]
            return book
        plugin.detail = detail
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["failed"] == 1 and not imported(sync)
    asyncio.run(scenario())


def test_remote_file_names_supported_without_guessing_sort_order(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        directory = existing_album(sync, plugin)
        plugin.pages = lambda comic_id, chapter_id: [f"https://images.invalid/{chapter_id}/page-b.jpg", f"https://images.invalid/{chapter_id}/page-a.jpg"]
        for chapter in directory.iterdir():
            (chapter / "00001.png").rename(chapter / "page-b.png")
            (chapter / "00002.png").rename(chapter / "page-a.png")
            (chapter / "cover.jpg").write_bytes(b"not a page")
            (chapter / "ComicInfo.xml").write_text("metadata")
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["local_albums"] == 1
        assert Path(imported(sync)[0]["path"]).name == "page-b.png"
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["skipped"] == 4 and not plugin.requests
    asyncio.run(scenario())


def test_new_chapters_and_changed_imported_files_are_not_skipped(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        directory = existing_album(sync, plugin)
        sync.launch(mode="check_local")
        await sync.task
        target = directory / "第 1 章" / "00001.png"
        target.write_bytes(b"corrupt external image")
        plugin.extra_chapter = True
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["status"] == "completed", sync.current
        assert sync.current["skipped"] == 3 and sync.current["downloaded"] == 3
        assert target.read_bytes() == b"corrupt external image"
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["skipped"] == 6 and not sync.current["downloaded"]
    asyncio.run(scenario())


def test_imported_output_is_never_owned_or_overwritten(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        directory = existing_album(sync, plugin, layout="id")
        sync.launch(mode="check_local")
        await sync.task
        target = directory / "1011" / "00001.png"
        target.write_bytes(b"user changed image")
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["status"] == "partial" and sync.current["failed"] == 1
        assert sync.current["skipped"] == 3 and target.read_bytes() == b"user changed image"
        with sync.store.connect() as db:
            assert not db.execute("SELECT * FROM library_jm_sync_files").fetchall()
    asyncio.run(scenario())


def test_owned_images_are_checked_but_not_imported(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        sync.launch(items=[("jm", "101")])
        await sync.task
        before = len(plugin.requests)
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["local_albums"] == 1 and sync.current["local_images"] == 4
        assert not imported(sync) and len(plugin.requests) == before
        # Keep managed output's existing format-change behavior.
        save(sync, image_format="webp")
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["downloaded"] == 4
    asyncio.run(scenario())


def test_missing_directory_is_not_created_by_check(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        root = jm_root(sync)
        assert not root.exists()
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["status"] == "completed" and sync.current["local_missing"] == 2 and not root.exists()
        assert not plugin.requests and not imported(sync)
    asyncio.run(scenario())


def test_scan_does_not_follow_symlink_directories(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        directory = existing_album(sync, plugin)
        moved = directory.rename(tmp_path / "outside")
        directory.symlink_to(moved, target_is_directory=True)
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["local_missing"] == 2 and not imported(sync)
    asyncio.run(scenario())


def test_new_root_does_not_reuse_old_imports(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        existing_album(sync, plugin)
        sync.launch(mode="check_local")
        await sync.task
        new_root = Path(sync.settings()["download_dir"]) / "new-root"
        save(sync, download_dir=str(new_root))
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["downloaded"] == 4 and not sync.current["skipped"]
    asyncio.run(scenario())


def test_check_can_be_cancelled_and_blocks_concurrent_work(tmp_path, monkeypatch):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        existing_album(sync, plugin)
        started = threading.Event()
        original = LocalLibrary.inspect
        def inspect(local, path):
            started.set()
            assert local.signal.wait(5)
            return original(local, path)
        monkeypatch.setattr(LocalLibrary, "inspect", inspect)
        sync.launch(mode="check_local")
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(.01)
        assert started.is_set()
        with pytest.raises(ComicApiError):
            sync.launch()
        with pytest.raises(ComicApiError):
            sync.save_settings(ImageSyncSettings())
        with pytest.raises(ComicApiError):
            JmFavorites(sync.store, sync.service, sync).guard_account_change()
        sync.cancel()
        await sync.task
        assert sync.current["status"] == "cancelled" and not imported(sync)
        assert not plugin.requests
    asyncio.run(scenario())


def test_changed_files_before_album_commit_are_not_registered(tmp_path, monkeypatch):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        directory = existing_album(sync, plugin)
        original = LocalLibrary.remember
        def remember(local, album_id, records):
            (directory / "第 1 章" / "00001.png").unlink()
            return original(local, album_id, records)
        monkeypatch.setattr(LocalLibrary, "remember", remember)
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["failed"] == 1 and not imported(sync)
    asyncio.run(scenario())


def test_changed_upstream_image_keys_are_not_skipped_once_pages_are_compared(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        directory = existing_album(sync, plugin)
        before = snapshot(directory)
        sync.launch(mode="check_local")
        await sync.task
        original = plugin.pages
        plugin.pages = lambda *args: [url.replace("/1.png", "/new-1.png") for url in original(*args)]
        # Unchanged metadata and files skip the whole comic without reading page lists.
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["skipped_albums"] == 1 and not sync.current["downloaded"] and not plugin.requests
        # Changed chapters invalidate that shortcut; pages whose image keys changed are downloaded again.
        plugin.extra_chapter = True
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert sync.current["skipped"] == 2 and sync.current["downloaded"] == 4
        assert {path: data for path, data in snapshot(directory).items() if path in before} == before
    asyncio.run(scenario())


def test_check_covers_only_library_books(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path, books=("101",))
        existing_album(sync, plugin)
        existing_album(sync, plugin, "102")
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["albums_total"] == sync.current["local_albums"] == 1
        assert [book["id"] for book in sync.store.books()] == ["101"]
    asyncio.run(scenario())


def test_existing_database_migration_keeps_download_checkpoints(tmp_path):
    sync, _ = make_sync(tmp_path)
    with sync.store.connect() as db:
        db.execute("INSERT INTO jm_sync_files VALUES ('scope','101','1011',1,'/1.png','/existing.png',1,'digest')")
        db.execute("DROP TABLE jm_sync_local_files")
        db.execute("PRAGMA user_version=2")
    migrated = Store(tmp_path)
    with migrated.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert db.execute("SELECT digest FROM jm_sync_files").fetchone()[0] == "digest"
        assert not db.execute("SELECT * FROM jm_sync_local_files").fetchall()


def test_local_check_api(tmp_path):
    from main import create_app
    sync, plugin = make_sync(tmp_path)
    existing_album(sync, plugin)
    with TestClient(create_app(sync.store, {"jm": plugin})) as client:
        response = client.post("/api/library/sync/check-local")
        assert response.status_code == 202 and response.json()["mode"] == "check_local"
        assert wait_for_run(client)["local_albums"] == 1
        html = client.get("/").text
        assert 'id="fav-sync-check-local"' in html and "「检查本地漫画」可识别，无需补 ID" in html
        assert client.post("/api/library/sync/run", json={"items": [{"source": "jm", "id": "101"}]}).status_code == 202
        assert wait_for_run(client)["skipped"] == 4 and not plugin.requests
        assert client.post("/api/library/sync/repair", json={"items": [{"source": "jm", "id": "101"}]}).status_code == 409


def local_rows(sync):
    with sync.store.connect() as db:
        return {row["album_id"]: dict(row) for row in db.execute("SELECT * FROM library_jm_local_status")}


@pytest.mark.parametrize("damage", ["corrupt", "missing_page", "extra_page", "missing_chapter"])
def test_check_records_status_and_repair_keeps_valid_pages(tmp_path, damage):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        directory = existing_album(sync, plugin)
        chapter = directory / "第 2 章"
        kept = (chapter / "00001.png").read_bytes(), (chapter / "00001.png").stat().st_mtime_ns
        if damage == "corrupt":
            (chapter / "00002.png").write_bytes(b"not an image")
        elif damage == "missing_page":
            (chapter / "00002.png").unlink()
        elif damage == "extra_page":
            (chapter / "00003.png").write_bytes(plugin.picture)
        else:
            for path in chapter.iterdir():
                path.unlink()
            chapter.rmdir()
        sync.launch(mode="check_local")
        await sync.task
        rows = local_rows(sync)
        assert rows["101"]["status"] == "error" and rows["101"]["path"] == str(directory)
        assert rows["102"]["status"] == "missing"
        assert sync.current["errors"][0]["album_id"] == "101"
        assert sync.local_counts() == {"downloaded": 0, "missing": 1, "error": 1, "repairable": 1}
        sync.launch(mode="repair")
        await sync.task
        state = sync.current
        assert state["mode"] == "repair" and state["status"] == "completed", state
        assert state["repaired_albums"] == 1 and state["downloaded"] == (2 if damage == "missing_chapter" else 0 if damage == "extra_page" else 1)
        assert state["moved_images"] == (1 if damage in ("corrupt", "extra_page") else 0)
        backup = Path(state["backup_dir"])
        if state["moved_images"]:
            assert backup.is_relative_to(Path(sync.settings()["download_dir"])) and any(backup.rglob("*.png"))
        if damage != "missing_chapter":
            assert ((chapter / "00001.png").read_bytes(), (chapter / "00001.png").stat().st_mtime_ns) == kept
        if damage == "missing_chapter":
            # A missing chapter directory is created with the configured naming rule.
            chapter = next(p for p in directory.iterdir() if p.name != "第 1 章")
        assert sorted(p.name for p in chapter.iterdir()) == ["00001.png", "00002.png"]
        assert local_rows(sync)["101"]["status"] == "downloaded" and len(imported(sync)) == 4
        # A follow-up check confirms the repaired album without touching the network.
        before = len(plugin.requests)
        sync.launch(mode="check_local")
        await sync.task
        assert sync.current["local_albums"] == 1 and not sync.current["failed"] and len(plugin.requests) == before
    asyncio.run(scenario())


def test_repair_requires_a_checked_error_and_never_targets_missing_albums(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        with pytest.raises(ComicApiError) as error:
            sync.launch(mode="repair")
        assert error.value.code == "nothing_to_repair"
        existing_album(sync, plugin)
        sync.launch(mode="check_local")
        await sync.task
        assert local_rows(sync)["101"]["status"] == "downloaded"
        with pytest.raises(ComicApiError) as error:
            sync.launch(mode="repair", items=[("jm", "101")])
        assert error.value.code == "not_repairable"
        with pytest.raises(ComicApiError) as error:
            sync.launch(mode="repair", items=[("jm", "102")])
        assert error.value.code == "not_repairable" and not plugin.requests
    asyncio.run(scenario())


def test_full_sync_records_downloaded_status_and_api_exposes_it(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        sync.launch(items=[("jm", "101")])
        await sync.task
        assert local_rows(sync)["101"]["status"] == "downloaded"
        assert local_rows(sync)["101"]["images"] == 4
        return sync
    sync = asyncio.run(scenario())
    data = sync.local_status("jm", ["101", "102"])
    assert set(data["items"]) == {"101"} and data["items"]["101"]["status"] == "downloaded"
    assert data["root"] == str(jm_root(sync))


def test_repair_rebaselines_changed_valid_image_and_moves_duplicates(tmp_path):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        directory = existing_album(sync, plugin)
        sync.launch(mode="check_local")
        await sync.task
        chapter = directory / "第 2 章"
        # A replaced but decodable page conflicts with the previous baseline; a duplicate page is extra.
        changed = io.BytesIO()
        Image.new("RGB", (3, 3), "red").save(changed, "PNG")
        (chapter / "00002.png").write_bytes(changed.getvalue())
        (chapter / "2.png").write_bytes(plugin.picture)
        sync.launch(mode="check_local")
        await sync.task
        assert local_rows(sync)["101"]["status"] == "error"
        before = len(plugin.requests)
        sync.launch(mode="repair", items=[("jm", "101")])
        await sync.task
        state = sync.current
        assert state["status"] == "completed", state
        assert state["moved_images"] == 1 and not state["downloaded"] and len(plugin.requests) == before
        assert (chapter / "00002.png").read_bytes() == changed.getvalue()
        assert sorted(p.name for p in chapter.iterdir()) == ["00001.png", "00002.png"]
        assert local_rows(sync)["101"]["status"] == "downloaded"
    asyncio.run(scenario())


def test_ambiguous_and_upstream_failures_are_not_repairable(tmp_path, monkeypatch):
    async def scenario():
        sync, plugin = make_sync(tmp_path)
        album = existing_album(sync, plugin, layout="title")
        (album.parent / "别名 [101]").mkdir()
        sync.launch(mode="check_local")
        await sync.task
        row = local_rows(sync)["101"]
        assert row["status"] == "error" and row["path"] == "" and "多个" in row["message"]
        assert sync.local_counts()["repairable"] == 0
        with pytest.raises(ComicApiError) as error:
            sync.launch(mode="repair")
        assert error.value.code == "nothing_to_repair"
        # An upstream failure during a later check keeps the last known local state.
        worker = sync.worker("jm")
        worker.save_local_status(worker.root(), "102", "downloaded", "漫画 102", "/x", 4)

        async def broken(*args, **kwargs):
            raise ComicApiError("上游超时", 502, "upstream_error", "jm")
        monkeypatch.setattr(worker, "_chapter_data", broken)
        existing_album(sync, plugin, "102")
        sync.launch(mode="check_local")
        await sync.task
        assert local_rows(sync)["102"]["status"] == "downloaded"
    asyncio.run(scenario())
