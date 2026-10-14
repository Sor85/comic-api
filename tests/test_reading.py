import pytest
from fastapi.testclient import TestClient

from src.storage import Store
from tests.fakes import DemoPlugin


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "store")


@pytest.fixture
def client(tmp_path, monkeypatch, store):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "default-data"))
    from main import create_app

    with TestClient(create_app(store, {"demo": DemoPlugin(store)})) as client:
        yield client


def position(chapter="c1", page=0, **values):
    return {"title": "Book", "cover": "https://images.invalid/c.jpg", "author": "A",
            "chapter_id": chapter, "chapter_name": f"Chapter {chapter}", "page": page, "total": 20, **values}


def test_saving_tracks_latest_position_and_every_chapter(client):
    assert client.put("/api/reading/demo/1", json=position("c1", 4)).status_code == 200
    assert client.put("/api/reading/demo/1", json=position("c2", 7)).status_code == 200
    client.put("/api/reading/demo/2", json=position("x", 1, title="Other"))

    items = client.get("/api/reading").json()["items"]
    assert [(item["id"], item["chapter_id"], item["page"]) for item in items] == [("2", "x", 1), ("1", "c2", 7)]
    assert all(item["in_history"] for item in items)

    detail = client.get("/api/reading/demo/1").json()
    assert detail["comic"]["chapter_name"] == "Chapter c2"
    assert {key: value["page"] for key, value in detail["chapters"].items()} == {"c1": 4, "c2": 7}
    assert client.get("/api/reading/demo/missing").json() == {"comic": None, "chapters": {}}


def test_clearing_history_keeps_progress_and_reading_again_restores_entry(client):
    client.put("/api/reading/demo/1", json=position("c1", 3))
    assert client.delete("/api/reading/history").json()["cleared"] == 1

    item = client.get("/api/reading").json()["items"][0]
    assert item["in_history"] is False and item["page"] == 3
    assert client.get("/api/reading/demo/1").json()["chapters"]["c1"]["page"] == 3

    client.put("/api/reading/demo/1", json=position("c1", 5))
    assert client.get("/api/reading").json()["items"][0]["in_history"] is True


def test_progress_survives_restart(tmp_path):
    first = Store(tmp_path)
    first.save_reading("demo", "1", position("c1", 9))
    again = Store(tmp_path)
    assert again.reading_comic("demo", "1")["chapters"]["c1"]["page"] == 9


def test_import_merges_browser_records_without_overwriting_newer_rows(client, store):
    store.save_reading("demo", "1", position("server", 2), updated_at=2000)
    comic = {**position("old", 8), "source": "demo", "comic_id": "1", "in_history": True, "updated_at": 1000}
    fresh = {**position("new", 1), "source": "demo", "comic_id": "2", "in_history": False, "updated_at": 1500}
    chapters = [{"source": "demo", "comic_id": "1", "chapter_id": "server", "page": 99, "updated_at": 1000},
                {"source": "demo", "comic_id": "1", "chapter_id": "old", "page": 8, "updated_at": 1000}]
    result = client.post("/api/reading/import", json={"comics": [comic, fresh], "chapters": chapters})
    assert result.status_code == 200

    one = store.reading_comic("demo", "1")
    assert one["comic"]["chapter_id"] == "server"
    assert {key: value["page"] for key, value in one["chapters"].items()} == {"server": 2, "old": 8}
    assert store.reading_comic("demo", "2")["comic"]["in_history"] is False


@pytest.mark.parametrize("body", [
    {**position(), "page": -1},
    {**position(), "chapter_id": ""},
    {key: value for key, value in position().items() if key != "chapter_id"},
])
def test_invalid_positions_are_rejected(client, body):
    assert client.put("/api/reading/demo/1", json=body).status_code == 422
    assert client.get("/api/reading").json()["items"] == []


def test_reading_responses_are_not_cached(client):
    client.put("/api/reading/demo/1", json=position())
    assert client.get("/api/reading").headers["cache-control"] == "no-store"
    assert client.get("/api/reading/demo/1").headers["cache-control"] == "no-store"
