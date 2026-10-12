import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.errors import ComicApiError
from src.services.jm_naming import JM_DIRECTORY_RULE
from src.services.jm_sync import SyncSettings
from tests.test_jm_sync import isolated_environment, make_sync, wait_for_run


def preflight_sync(tmp_path):
    sync, plugin = make_sync(tmp_path)
    sync.save_settings(SyncSettings(**dict(sync.settings(), directory_rule=JM_DIRECTORY_RULE)))
    Path(sync.settings()["download_dir"]).mkdir()
    return sync, plugin


def favorite_items(plugin, title="失效收藏"):
    plugin.favorites = lambda **kwargs: {
        "items": [{"id": "101", "title": "正常收藏"}, {"id": "102", "title": title}, {"id": "103", "title": "尚未处理"}],
        "has_next": False, "total": 3,
    }


@pytest.mark.parametrize("title", ["  已下架漫画  ", None, "   "])
@pytest.mark.parametrize("status, code", [(404, "not_found"), (401, "login_required"), (502, "upstream_error")])
def test_preflight_failure_records_identity_and_phase_without_continuing(tmp_path, title, status, code):
    async def scenario():
        sync, plugin = preflight_sync(tmp_path)
        favorite_items(plugin, title)
        original = plugin.detail
        calls = []
        def detail(comic_id):
            calls.append(comic_id)
            if comic_id == "102":
                raise ComicApiError("模拟详情读取失败", status, code, "jm")
            return original(comic_id)
        plugin.detail = detail
        plugin.chapter_detail = lambda *args: pytest.fail("Image validation must not start during metadata preflight")
        sync.launch(check_local=True)
        await sync.task
        state = sync.current
        expected_title = title.strip() if isinstance(title, str) else ""
        assert state["status"] == "failed" and state["stage"] == "finished"
        assert state["failed"] == 1 and state["albums_done"] == 0 and state["albums_total"] == 3
        assert state["local_albums"] == state["local_images"] == state["downloaded"] == 0
        assert state["title"] == (expected_title or "102")
        assert state["errors"] == [{"stage": "matching", "album_id": "102", "title": expected_title,
                                    "message": "模拟详情读取失败", "code": code}]
        assert "详情预读取失败" in state["error"] and "102" in state["error"]
        assert calls == ["101", "102"] and not plugin.requests
        assert sync.history()[0]["errors"] == state["errors"]
        with sync.store.connect() as db:
            assert not db.execute("SELECT * FROM jm_sync_local_files").fetchall()
        assert not sync.store.books()
    asyncio.run(scenario())


def test_unknown_preflight_error_is_sanitized(tmp_path):
    async def scenario():
        sync, plugin = preflight_sync(tmp_path)
        favorite_items(plugin)
        async def broken_detail(*args):
            raise RuntimeError("internal diagnostic must not reach the response")
        sync.service.get_comic_detail = broken_detail
        sync.launch(check_local=True)
        await sync.task
        error = sync.current["errors"][0]
        assert error["message"] == "图源详情读取失败" and error["code"] == "upstream_error"
        assert "internal diagnostic" not in str(sync.status())
        assert sync.current["failed"] == 1 and not plugin.requests
    asyncio.run(scenario())


def test_cancel_during_failing_preflight_is_not_reported_as_detail_failure(tmp_path):
    async def scenario():
        sync, plugin = preflight_sync(tmp_path)
        favorite_items(plugin)
        async def cancelled_detail(*args):
            sync.cancel()
            raise ComicApiError("请求结束时发生错误", 502, "upstream_error", "jm")
        sync.service.get_comic_detail = cancelled_detail
        sync.launch(check_local=True)
        await sync.task
        assert sync.current["status"] == "cancelled"
        assert sync.current["failed"] == 0 and sync.current["errors"] == [] and not sync.current["error"]
    asyncio.run(scenario())


def test_preflight_failure_preserves_files_existing_checkpoints_and_library(tmp_path):
    async def scenario():
        sync, plugin = preflight_sync(tmp_path)
        # Keep both managed downloads and previously imported external files.
        sync.launch(["101"])
        await sync.task
        assert sync.current["downloaded"] == 4
        book = plugin.detail("102")
        for index, chapter in enumerate(book["chapters"], 1):
            detail = plugin.chapter_detail("102", chapter["id"])
            directory = sync.paths.chapter(sync.settings(), book, detail, index)
            directory.mkdir(parents=True)
            for page in (1, 2):
                (directory / f"{page:05d}.png").write_bytes(plugin.picture)
        sync.launch(check_local=True)
        await sync.task
        assert sync.current["local_albums"] == 2
        root = Path(sync.settings()["download_dir"])
        def snapshot():
            with sync.store.connect() as db:
                return {
                    "files": {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()},
                    "managed": [tuple(row) for row in db.execute("SELECT * FROM jm_sync_files ORDER BY album_id,chapter_id,page")],
                    "imported": [tuple(row) for row in db.execute("SELECT * FROM jm_sync_local_files ORDER BY album_id,chapter_id,page")],
                    "library": [tuple(row) for row in db.execute("SELECT * FROM library ORDER BY source,comic_id")],
                }
        before, received, next_run = snapshot(), len(plugin.requests), sync.next_run_at
        assert len(before["managed"]) == len(before["imported"]) == 4
        plugin.favorites = lambda **kwargs: {"items": [{"id": cid, "title": f"收藏 {cid}"} for cid in ("101", "102", "103")],
                                            "has_next": False, "total": 3}
        original = plugin.detail
        def detail(comic_id):
            if comic_id == "103":
                raise ComicApiError("漫画已删除", 404, "not_found", "jm")
            return original(comic_id)
        plugin.detail = detail
        sync.launch(check_local=True)
        await sync.task
        assert sync.current["status"] == "failed" and sync.current["errors"][0]["album_id"] == "103"
        assert sync.current["albums_done"] == sync.current["local_albums"] == sync.current["local_images"] == 0
        assert snapshot() == before and len(plugin.requests) == received and sync.next_run_at == next_run
    asyncio.run(scenario())


def test_preflight_context_is_returned_by_api_and_survives_restart(tmp_path):
    from main import create_app
    sync, plugin = preflight_sync(tmp_path)
    favorite_items(plugin, "失效漫画 <示例>")
    original = plugin.detail
    def detail(comic_id):
        if comic_id == "102":
            raise ComicApiError("漫画不可访问", 404, "not_found", "jm")
        return original(comic_id)
    plugin.detail = detail
    with TestClient(create_app(sync.store, {"jm": plugin})) as client:
        assert client.post("/api/jm/sync/check-local").status_code == 202
        run = wait_for_run(client)
        assert run["status"] == "failed" and run["failed"] == 1
        assert run["errors"][0]["title"] == "失效漫画 <示例>" and run["errors"][0]["album_id"] == "102"
        assert run["errors"][0]["stage"] == "matching"
    with TestClient(create_app(sync.store, {"jm": plugin})) as client:
        status = client.get("/api/jm/sync").json()
        assert status["current"] is None and status["history"][0]["errors"] == run["errors"]
        assert status["history"][0]["error"] == run["error"]
