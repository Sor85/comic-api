import asyncio
import sqlite3

import pytest

from src.errors import ComicApiError
from src.services.aggregator import AggregatorService
from src.services.jm_sync import JmSync
from src.storage import Store
from tests.test_jm_favorites import favorite_client


def test_partial_full_and_unknown_states_are_account_scoped(tmp_path):
    store = Store(tmp_path)
    first, second = store.account_owner("first"), store.account_owner("second")
    store.record_jm_favorites(first, ["000101"])
    store.record_jm_favorites(second, ["202"])
    assert store.jm_favorite_status(first, "101") is True
    assert store.jm_favorite_status(first, "202") is None
    store.record_jm_favorites(first, ["102"], complete=True)
    assert store.jm_favorite_status(first, "101") is False
    assert store.jm_favorite_status(first, "102") is True
    store.set_jm_favorite(first, "102", None)
    assert store.jm_favorite_status(first, "102") is None
    store.record_jm_favorites(first, ["102"])
    assert store.jm_favorite_status(first, "102") is True
    store.record_jm_favorites(first, [], complete=True)
    assert store.jm_favorite_status(first, "102") is False
    assert store.jm_favorite_status(second, "202") is True
    assert store.account_owner("") is None
    store.record_jm_favorites(None, ["101"], complete=True)
    assert store.jm_favorite_status(None, "101") is None


def test_atomic_replacement_rolls_back_on_insert_failure(tmp_path):
    store = Store(tmp_path)
    owner = store.account_owner("test")
    store.record_jm_favorites(owner, ["101"], complete=True)
    with store.connect() as db:
        db.executescript("""CREATE TRIGGER fail_test_insert BEFORE INSERT ON jm_favorites
            WHEN NEW.comic_id='999' BEGIN SELECT RAISE(ABORT,'test failure'); END;""")
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        store.record_jm_favorites(owner, ["102", "999"], complete=True)
    assert store.jm_favorite_status(owner, "101") is True
    assert store.jm_favorite_status(owner, "102") is False


def test_v3_database_migrates_without_losing_existing_data(tmp_path):
    original = Store(tmp_path)
    original.save_account("jm", {"account": "test", "token": "demo-session"})
    original.save_book("jm", "101", {"id": "101", "title": "Existing"}, "Existing category")
    with original.connect() as db:
        db.execute("DROP TABLE jm_favorites")
        db.execute("DROP TABLE jm_favorite_catalog")
        db.execute("PRAGMA user_version=3")
    migrated = Store(tmp_path)
    with migrated.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
    assert migrated.account("jm")["account"] == "test"
    assert migrated.books()[0]["library_category"] == "Existing category"
    owner = migrated.account_owner("test")
    assert migrated.jm_favorite_status(owner, "101") is None
    migrated.record_jm_favorites(owner, ["101"], complete=True)
    reopened = Store(tmp_path)
    assert reopened.jm_favorite_status(owner, "101") is True
    assert reopened.jm_favorite_status(owner, "102") is False


def test_sync_full_scan_updates_catalog_before_downloads(favorite_client, monkeypatch):
    monkeypatch.delenv("JM_DOWNLOAD_ROOTS", raising=False)
    _, remote, plugin, store = favorite_client
    async def scenario():
        sync = JmSync(store, AggregatorService(store, {"jm": plugin}))
        owner = sync.identity()
        store.record_jm_favorites(owner, ["999"])
        sync.current = sync._new_state(False)
        data = await sync._collect(sync.settings(), owner)
        assert set(data) == remote.ids
        assert store.jm_favorite_status(owner, "101") is True
        assert store.jm_favorite_status(owner, "999") is False
    asyncio.run(scenario())


def test_sync_custom_folder_does_not_delete_other_favorites(favorite_client, monkeypatch):
    monkeypatch.delenv("JM_DOWNLOAD_ROOTS", raising=False)
    client, _, plugin, store = favorite_client
    payload = {"list": [{"id": "101", "name": "Scoped"}],
               "folder_list": [{"FID": "7", "name": "Scope"}], "total": "1"}
    monkeypatch.setattr(client, "jm_request", lambda *a, **k: payload)
    async def scenario():
        sync = JmSync(store, AggregatorService(store, {"jm": plugin}))
        owner = sync.identity()
        store.record_jm_favorites(owner, ["999"])
        sync.current = sync._new_state(False)
        config = dict(sync.settings(), folder_ids=["7"])
        assert set(await sync._collect(config, owner)) == {"101"}
        assert store.jm_favorite_status(owner, "101") is True
        assert store.jm_favorite_status(owner, "999") is True
        assert store.jm_favorite_status(owner, "102") is None
    asyncio.run(scenario())


def test_failed_sync_keeps_pulled_rows_without_completing_snapshot(favorite_client, monkeypatch):
    monkeypatch.delenv("JM_DOWNLOAD_ROOTS", raising=False)
    client, remote, plugin, store = favorite_client
    original = remote.request
    def broken(method, url, **kwargs):
        if kwargs.get("params", {}).get("page") == "2":
            raise ComicApiError("第二页失败", 502, "upstream_error", "jm")
        return original(method, url, **kwargs)
    monkeypatch.setattr(client, "request", broken)
    async def scenario():
        sync = JmSync(store, AggregatorService(store, {"jm": plugin}))
        owner = sync.identity()
        store.record_jm_favorites(owner, ["999"])
        sync.current = sync._new_state(False)
        with pytest.raises(ComicApiError, match="第二页失败"):
            await sync._collect(sync.settings(), owner)
        assert store.jm_favorite_status(owner, "101") is True
        assert store.jm_favorite_status(owner, "999") is True
        assert store.jm_favorite_status(owner, "121") is None
    asyncio.run(scenario())
