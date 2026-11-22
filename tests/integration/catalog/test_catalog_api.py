"""Read-only routes over the fixture catalogue, through the app with its guards."""

import asyncio
import os
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from manga_atlas.config import Settings
from manga_atlas.query.cover import Covers
from manga_atlas.web.app import create_app

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def test_search(client: TestClient) -> None:
    r = client.get("/api/search", params={"t": "百合,校园", "x": "恐怖"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == 101 and item["cover"] == "/cover/101" and item["readable"] == "unknown"
    assert set(item) >= {"id", "title", "name", "year", "status", "region", "score", "rank"}
    assert item["authors"] == [{"id": 1, "name": "仲谷鳰"}]  # not the publisher or magazine
    assert item["tags"][:2] == [{"name": "百合", "grp": "genre"}, {"name": "校园", "grp": "genre"}]
    assert item["summary"] == "两个女孩。"
    assert set(body["facets"]) == {"tags", "region", "status", "included", "excluded", "people"}
    assert body["facets"]["people"] == []
    assert body["facets"]["included"] == [
        {"query": "百合", "name": "百合", "grp": "genre"},
        {"query": "校园", "name": "校园", "grp": "genre"},
    ]
    assert body["facets"]["excluded"] == [{"query": "恐怖", "name": "恐怖", "grp": "genre", "n": 1}]


def test_search_params(client: TestClient) -> None:
    def total(**params: str) -> int:
        r = client.get("/api/search", params=params)
        assert r.status_code == 200, r.text
        return int(r.json()["total"])

    assert total(t="GL") == 2
    assert total(t="百合，校园") == 2  # full-width comma
    assert total(y="2010-2019") == 5 and total(y="-2008") == 3 and total(y="2015") == 1
    assert total(st="完结") == 3 and total(region="韩国") == 2 and total(min="8") == 2
    assert total(a="61") == 2 and total(a="61,62") == 1
    assert total(q="浦沢") == 2 and total(read="1") == 0
    assert "tag_header" in client.get("/api/search", params={"t": "校园"}).json()


def test_search_names_the_people_it_is_limited_to(client: TestClient) -> None:
    body = client.get("/api/search", params={"a": "61,50,51"}).json()
    assert body["facets"]["people"] == [  # in the order asked, each with the role held most
        {"id": 61, "name": "甲山", "role": "作者", "creator": True},
        {"id": 50, "name": "集英社", "role": "出版社", "creator": False},
        {"id": 51, "name": "コミック電撃大王", "role": "连载杂志", "creator": False},
    ]


def test_search_paging(client: TestClient, settings: Settings) -> None:
    page = client.get("/api/search", params={"sort": "heat", "offset": 2, "limit": 2}).json()
    assert [i["id"] for i in page["items"]] == [103, 102] and page["total"] == 9
    huge = client.get("/api/search", params={"limit": settings.search_max_limit * 10})
    assert huge.status_code == 200 and len(huge.json()["items"]) == 9


@pytest.mark.parametrize(
    "params", [{"sort": "nope"}, {"y": "20x0"}, {"a": "abc"}, {"limit": "0"}, {"offset": "-1"}]
)
def test_search_rejects_bad_params(client: TestClient, params: dict[str, str]) -> None:
    assert client.get("/api/search", params=params).status_code == 422


def test_suggest_and_work(client: TestClient) -> None:
    got = client.get("/api/suggest", params={"q": "尸鬼"}).json()
    assert got["works"][0]["id"] == 103 and got["tags"] == []
    detail = client.get("/api/work/101").json()
    assert detail["title"] == "终将成为你" and detail["people"][0]["name"] == "仲谷鳰"
    assert client.get("/api/work/105").status_code == 404
    assert client.get("/api/work/abc").status_code == 422


def test_missing_catalogue_is_503(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    client = TestClient(create_app(settings), base_url=f"http://{settings.host}:{settings.port}")
    for path in ("/api/search", "/api/suggest?q=x", "/api/work/1"):
        assert client.get(path).status_code == 503
    assert client.get("/cover/1").status_code == 404


class FakeBangumi:
    """Answers the image endpoint like Bangumi: a redirect to the picture or a placeholder."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if "/v0/subjects/101/" in str(request.url):
            return httpx.Response(302, headers={"location": "https://img.test/pic/cover/101.jpg"})
        if "/v0/subjects/102/" in str(request.url):
            target = "https://img.test" + self.settings.cover_placeholder
            return httpx.Response(302, headers={"location": target})
        if "/v0/subjects/103/" in str(request.url):
            raise httpx.ConnectError("down")
        if request.url.host == "img.test" and request.url.path.endswith("101.jpg"):
            assert request.headers["user-agent"] == self.settings.user_agent
            return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})
        return httpx.Response(200, content=b"<html>placeholder</html>")


@pytest.fixture
def bangumi(client: TestClient, settings: Settings) -> FakeBangumi:
    fake = FakeBangumi(settings)
    client.app.state.covers = Covers(settings, transport=httpx.MockTransport(fake))  # type: ignore[attr-defined]
    return fake


def test_cover_is_fetched_once_then_served_from_disk(
    client: TestClient, settings: Settings, bangumi: FakeBangumi
) -> None:
    r = client.get("/cover/101")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content == JPEG
    assert (settings.covers_dir / "101").read_bytes() == JPEG
    assert client.get("/cover/101").status_code == 200
    assert len(bangumi.calls) == 2  # endpoint + redirect, once


def test_cover_misses_are_remembered_for_a_while(
    client: TestClient, settings: Settings, bangumi: FakeBangumi
) -> None:
    assert client.get("/cover/102").status_code == 404  # Bangumi has no picture
    assert client.get("/cover/103").status_code == 404  # Bangumi unreachable
    calls = len(bangumi.calls)
    assert client.get("/cover/102").status_code == 404
    assert client.get("/cover/103").status_code == 404
    assert len(bangumi.calls) == calls
    marker = settings.covers_dir / "103"
    old = time.time() - settings.cover_miss_ttl_s - 1
    os.utime(marker, (old, old))
    client.get("/cover/103")
    assert len(bangumi.calls) == calls + 1  # tried again after the wait


def test_one_download_per_cover_at_a_time(settings: Settings) -> None:
    calls: list[str] = []

    async def bangumi(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        await asyncio.sleep(0.05)  # both tabs ask while this is in flight
        return httpx.Response(200, content=JPEG) if len(calls) == 1 else httpx.Response(500)

    covers = Covers(settings, transport=httpx.MockTransport(bangumi))

    async def two_tabs() -> list[object]:
        return await asyncio.gather(covers.fetch(101), covers.fetch(101))

    first, second = asyncio.run(two_tabs())
    assert len(calls) == 1 and first is not None and first == second
    assert (settings.covers_dir / "101").read_bytes() == JPEG


def test_a_failed_download_keeps_a_cover_stored_meanwhile(settings: Settings) -> None:
    path = settings.covers_dir / "101"

    def bangumi(request: httpx.Request) -> httpx.Response:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(JPEG)  # another process stored it while we were asking
        return httpx.Response(500)

    covers = Covers(settings, transport=httpx.MockTransport(bangumi))
    assert asyncio.run(covers.fetch(101)) is not None
    assert path.read_bytes() == JPEG


def test_cover_only_for_catalogue_works(client: TestClient, bangumi: FakeBangumi) -> None:
    assert client.get("/cover/105").status_code == 404  # adult, not in the catalogue
    assert client.get("/cover/999").status_code == 404
    assert bangumi.calls == []
