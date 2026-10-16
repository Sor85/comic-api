import asyncio

import pytest
from fastapi.testclient import TestClient

from main import create_app
from src.errors import ComicApiError
from src.services.aggregator import AggregatorService
from src.services.bika_sync import BikaSync
from src.services.favorite_sync import BaseSyncSettings
from src.storage import Store
from tests.test_bika_sync import BikaFavoritePlugin, isolated_environment  # noqa: F401

A, B, C = "5e7f2a9c1b3d4e6f8a0b2c4d", "5e7f2a9c1b3d4e6f8a0b2c4e", "5e7f2a9c1b3d4e6f8a0b2c4f"


class CatalogPlugin(BikaFavoritePlugin):
    """Three favorites, one per page; ``changing`` makes the total move mid-scan."""

    ids = [A, B, C]
    changing = False

    def favorites(self, page=1, sort="dd"):
        self.favorite_calls.append((page, sort))
        ids = self.ids if sort != "da" else list(reversed(self.ids))
        total = len(ids) + (1 if self.changing and page > 1 else 0)
        return {"items": [self.detail(ids[page - 1])] if page <= len(ids) else [], "total": total,
                "page": page, "page_size": 1, "has_next": page < len(ids)}


def make_sync(tmp_path):
    store = Store(tmp_path)
    plugin = CatalogPlugin(store)
    sync = BikaSync(store, AggregatorService(store, {"bika": plugin}))
    sync.save_settings(BaseSyncSettings(directory_rule="title", retries=0))
    return sync, plugin


def test_store_keeps_items_and_snapshot_order_and_joins_local_state(tmp_path):
    store = Store(tmp_path)
    owner = store.account_owner("reader")
    store.record_bika_favorites(owner, [A], items={A: {"id": A, "title": "旧标题"}})
    store.record_bika_favorites(owner, [C, A, B], complete=True, sort="dd",
                                items={C: {"id": C, "title": "丙"}, B: {"id": B, "title": "乙"}})
    store.set_bika_favorite(owner, "5e7f2a9c1b3d4e6f8a0b2c50", True)
    with store.connect() as db:
        db.execute("INSERT INTO bika_local_status VALUES (?,?,?,?,?,?,?,?)", ("/root", A, "downloaded", "", "/root/a", 3, "", 1.0))
        db.execute("INSERT INTO bika_local_status VALUES (?,?,?,?,?,?,?,?)", ("/other", B, "downloaded", "", "/other/b", 3, "", 1.0))
    data = store.favorite_catalog("bika", owner, "/root")
    assert data["complete"] and data["sort"] == "dd"
    # Added after the snapshot: first, without stored item; then the snapshot order.
    assert [row["comic_id"] for row in data["rows"]] == ["5e7f2a9c1b3d4e6f8a0b2c50", C, A, B]
    rows = {row["comic_id"]: row for row in data["rows"]}
    assert rows[A]["item"] and "旧标题" in rows[A]["item"]  # complete snapshot without item keeps the stored one
    assert rows[A]["status"] == "downloaded" and rows[B]["status"] is None  # other save roots do not count
    store.record_bika_favorites(owner, [B], complete=True)
    data = store.favorite_catalog("bika", owner, "/root")
    assert [row["comic_id"] for row in data["rows"]] == [B] and data["sort"] == "dd"
    assert store.favorite_catalog("bika", store.account_owner("other"), "/root") == {
        "complete": False, "updated_at": None, "sort": "", "rows": []}


def test_catalog_filters_whole_collection_and_paginates(tmp_path):
    sync, _ = make_sync(tmp_path)
    owner = sync.owner()
    sync.store.record_bika_favorites(owner, [A, B, C], complete=True, sort="dd",
                                     items={key: {"id": key, "title": key[-1]} for key in (A, B, C)})
    root = sync.settings()["download_dir"]
    sync.save_local_status(root, A, "downloaded", "a", "/x/a", 4)
    sync.save_local_status(root, B, "error", "b", "/x/b", message="坏图")
    data = sync.catalog("missing", page=1, page_size=1)
    assert data["counts"] == {"all": 3, "downloaded": 1, "missing": 2}
    assert data["total"] == 2 and data["has_next"] and [item["id"] for item in data["items"]] == [B]
    assert data["local"][B]["status"] == "error" and data["items"][0]["source"] == "bika"
    data = sync.catalog("missing", page=2, page_size=1)
    assert [item["id"] for item in data["items"]] == [C] and not data["has_next"] and data["local"] == {}
    assert [item["id"] for item in sync.catalog("downloaded")["items"]] == [A]
    assert len(sync.catalog()["items"]) == 3


def test_refresh_reads_every_page_and_replaces_catalog(tmp_path):
    asyncio.run(_refresh(tmp_path))


async def _refresh(tmp_path):
    sync, plugin = make_sync(tmp_path)
    sync.store.record_bika_favorites(sync.owner(), ["5e7f2a9c1b3d4e6f8a0b2c50"], complete=True)
    state = sync.refresh_catalog("da")
    assert state["active"] and state["sort"] == "da"
    with pytest.raises(ComicApiError) as error:
        sync.refresh_catalog()
    assert error.value.code == "catalog_active"
    await sync.catalog_task
    assert plugin.favorite_calls == [(1, "da"), (2, "da"), (3, "da")]
    data = sync.catalog()
    assert data["complete"] and data["sort"] == "da" and data["refresh"]["pages"] == 3 and not data["refresh"]["active"]
    assert [item["id"] for item in data["items"]] == [C, B, A]
    assert data["items"][0]["title"] == "哔咔漫画 f"
    with pytest.raises(ComicApiError):
        sync.refresh_catalog("xx")


def test_failed_refresh_keeps_previous_catalog(tmp_path):
    asyncio.run(_failed_refresh(tmp_path))


async def _failed_refresh(tmp_path):
    sync, plugin = make_sync(tmp_path)
    sync.store.record_bika_favorites(sync.owner(), [A], complete=True, sort="dd")
    plugin.changing = True
    sync.refresh_catalog()
    await sync.catalog_task
    data = sync.catalog()
    assert "变化" in data["refresh"]["error"] and [item["id"] for item in data["items"]] == [A]


def test_catalog_api_validates_filter(tmp_path):
    store = Store(tmp_path)
    plugin = CatalogPlugin(store)
    with TestClient(create_app(store, {"bika": plugin})) as client:
        response = client.get("/api/bika/sync/catalog", params={"local": "missing"})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json()["complete"] is False
        assert client.get("/api/bika/sync/catalog", params={"local": "broken"}).status_code == 422
        assert client.post("/api/bika/sync/catalog/refresh", params={"sort": "dd"}).status_code == 202
