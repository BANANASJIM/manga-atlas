from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from tests.suwayomi_support import FakeSuwayomi, Work, make_atlas

from manga_atlas.config import Settings
from manga_atlas.suwayomi import LibraryManga
from manga_atlas.web.app import create_app

HEADERS = {"X-Atlas": "1"}


@pytest.fixture
def fake() -> FakeSuwayomi:
    f = FakeSuwayomi()
    f.items = [LibraryManga(2637, "魔男伊奇", "宇佐崎 しろ, 西修", None, "zh-b", ())]
    return f


@pytest.fixture
def client(settings: Settings, fake: FakeSuwayomi) -> Iterator[TestClient]:
    app = create_app(settings)
    app.state.suwayomi_client_factory = lambda _settings: fake
    with TestClient(app, base_url=f"http://{settings.host}:{settings.port}") as c:
        yield c


def test_library_lists_mapped_entries(
    client: TestClient, settings: Settings, fake: FakeSuwayomi
) -> None:
    make_atlas(settings.atlas_db, [Work(9, "魔男のイチ", "魔男伊奇", tags={"奇幻": 4})])
    body = client.get("/api/library").json()
    assert body["reachable"] is True
    [item] = body["items"]
    assert (item["manga_id"], item["work_id"], item["source"]) == (2637, 9, "中文源乙")
    fake.down = True
    client.app.state.availability_service.library_synced = (
        None  # let the next GET try Suwayomi again
    )
    body = client.get("/api/library").json()
    assert body["reachable"] is False and body["items"][0]["work_id"] == 9  # served from the mirror


def test_organize_routes(client: TestClient, settings: Settings, fake: FakeSuwayomi) -> None:
    assert client.post("/api/organize", json={"tags": ["奇幻"]}).status_code == 403
    assert client.post("/api/organize", json={"tags": [" #"]}, headers=HEADERS).status_code == 422
    assert client.post("/api/organize", json={"tags": ["奇幻"]}, headers=HEADERS).status_code == 503
    make_atlas(settings.atlas_db, [Work(9, "魔男のイチ", "魔男伊奇", tags={"奇幻": 4})])
    preview = client.post("/api/organize", json={"tags": ["奇幻"]}, headers=HEADERS).json()
    assert preview["apply"] is False and preview["categories"][0]["mangas"][0]["manga_id"] == 2637
    assert not fake.of("create_category")
    applied = client.post("/api/organize", json={"tags": ["奇幻"], "apply": True}, headers=HEADERS)
    assert applied.json()["created"] == ["奇幻"] and applied.json()["added"] == 1
    fake.down = True
    down = client.post("/api/organize", json={"tags": ["奇幻"]}, headers=HEADERS)
    assert down.status_code == 503
