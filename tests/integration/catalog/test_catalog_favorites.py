"""Favorites through the app with its guards: add and remove, then search and the work page."""

from pathlib import Path

from fastapi.testclient import TestClient

from manga_atlas.availability import state
from manga_atlas.config import Settings
from manga_atlas.web.app import create_app

HEADERS = {"X-Atlas": "1"}


def favorites(settings: Settings) -> dict[int, str]:
    with state.connect(settings) as con:
        return dict(con.execute("SELECT work_id, added_at FROM favorite"))


def test_add_and_remove(client: TestClient, settings: Settings) -> None:
    assert client.put("/api/favorites/101").status_code == 403  # writes need the header
    r = client.put("/api/favorites/101", headers=HEADERS)
    assert (r.status_code, r.content) == (204, b"")
    assert list(favorites(settings)) == [101]
    assert client.get("/api/work/101").json()["favorite"] is True
    assert client.get("/api/work/102").json()["favorite"] is False
    body = client.get("/api/search", params={"fav": "1", "sort": "fav"}).json()
    assert [(i["id"], i["favorite"]) for i in body["items"]] == [(101, True)]

    with state.connect(settings) as con:
        con.execute("UPDATE favorite SET added_at = '2020-01-01T00:00:00Z'")
    assert client.put("/api/favorites/101", headers=HEADERS).status_code == 204
    assert favorites(settings) == {101: "2020-01-01T00:00:00Z"}  # added again: keeps its time

    assert client.delete("/api/favorites/101").status_code == 403
    assert client.delete("/api/favorites/101", headers=HEADERS).status_code == 204
    assert client.delete("/api/favorites/101", headers=HEADERS).status_code == 204
    assert favorites(settings) == {}
    assert client.get("/api/work/101").json()["favorite"] is False
    assert client.get("/api/search", params={"fav": "1"}).json()["total"] == 0


def test_only_catalogue_works(client: TestClient, settings: Settings) -> None:
    assert client.put("/api/favorites/105", headers=HEADERS).status_code == 404  # adult
    assert client.put("/api/favorites/999", headers=HEADERS).status_code == 404
    assert favorites(settings) == {}


def test_missing_catalogue_is_503(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    client = TestClient(create_app(settings), base_url=f"http://{settings.host}:{settings.port}")
    assert client.put("/api/favorites/101", headers=HEADERS).status_code == 503
