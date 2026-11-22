import asyncio
import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.suwayomi_support import FakeSuwayomi, Work, make_atlas, manga

from manga_atlas.availability import warm_async
from manga_atlas.config import Settings, Source
from manga_atlas.suwayomi import MangaState, SourceFailure
from manga_atlas.web.app import create_app

HEADERS = {"X-Atlas": "1"}
WORKS = [
    Work(1, "進撃の巨人", "进击的巨人", authors=("諫山創",), collects=900),
    Work(2, "ピンポン", "乒乓", authors=("松本大洋",), collects=800),
]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    base = Settings(data_dir=tmp_path)
    return replace(
        base,
        sources=(Source("z1", "中一"), Source("z2", "中二"), Source("off", "关", enabled=False)),
        source_interval_s=0.0,
        source_timeout_s=1.0,
    )


@pytest.fixture
def fake() -> FakeSuwayomi:
    f = FakeSuwayomi()
    f.searches[("z1", "进击的巨人")] = [manga(10, "進擊的巨人", author="諫山創")]
    f.searches[("z2", "乒乓")] = [manga(21, "乒乓")]
    return f


@pytest.fixture
def client(settings: Settings, fake: FakeSuwayomi) -> Iterator[TestClient]:
    app = create_app(settings)
    app.state.suwayomi_client_factory = lambda _settings: fake
    with TestClient(app, base_url=f"http://{settings.host}:{settings.port}") as c:
        yield c


def ndjson(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line]


def test_work_check_streams_one_line_per_enabled_source(
    client: TestClient, settings: Settings, fake: FakeSuwayomi
) -> None:
    make_atlas(settings.atlas_db, WORKS)
    r = client.post("/api/work/1/check", headers=HEADERS)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = {line["source"]: line for line in ndjson(r.text)}
    assert set(lines) == {"z1", "z2"}
    assert lines["z1"] | {"checked_at": None} == {
        "source": "z1",
        "name": "中一",
        "state": "yes",
        "query": "进击的巨人",
        "manga_id": 10,
        "title": "進擊的巨人",
        "renamed": False,
        "author_match": True,
        "cached": False,
        "checked_at": None,
    }
    assert lines["z2"]["state"] == "no"
    again = {
        line["source"]: line
        for line in ndjson(client.post("/api/work/1/check", headers=HEADERS).text)
    }
    assert again["z1"]["cached"] is True
    searches = len(fake.of("search"))
    cached = client.post("/api/work/1/check", json={}, headers=HEADERS)
    assert {line["cached"] for line in ndjson(cached.text)} == {True}
    assert len(fake.of("search")) == searches
    fresh = client.post("/api/work/1/check", json={"force": True}, headers=HEADERS)
    assert {line["cached"] for line in ndjson(fresh.text)} == {False}
    assert len(fake.of("search")) == searches + 2  # every enabled source asked again


def test_same_short_title_by_another_author(
    client: TestClient, settings: Settings, fake: FakeSuwayomi
) -> None:
    make_atlas(settings.atlas_db, WORKS)
    fake.searches[("z2", "乒乓")] = [manga(21, "乒乓", author="Melord花爷")]
    lines = {
        line["source"]: line
        for line in ndjson(client.post("/api/work/2/check", headers=HEADERS).text)
    }
    assert (lines["z2"]["state"], lines["z2"]["title"], lines["z2"]["author_match"]) == (
        "no",
        "乒乓",
        False,
    )
    page = ndjson(client.post("/api/check", json={"ids": [2]}, headers=HEADERS).text)
    assert page[0]["readable"] == "no"


def test_work_check_errors(client: TestClient, settings: Settings) -> None:
    assert client.post("/api/work/1/check", headers=HEADERS).status_code == 503  # no catalogue
    make_atlas(settings.atlas_db, WORKS)
    assert client.post("/api/work/999/check", headers=HEADERS).status_code == 404
    assert client.post("/api/work/1/check").status_code == 403  # writes need the header


def test_page_check_reports_progress(client: TestClient, settings: Settings) -> None:
    make_atlas(settings.atlas_db, WORKS)
    r = client.post("/api/check", json={"ids": [1, 2, 1]}, headers=HEADERS)
    assert r.status_code == 200
    lines = ndjson(r.text)
    assert {line["id"]: line["readable"] for line in lines} == {1: "yes", 2: "maybe"}
    assert sorted(line["done"] for line in lines) == [1, 2] and lines[0]["total"] == 2
    assert client.post("/api/check", json={"ids": []}, headers=HEADERS).status_code == 422
    too_many = list(range(settings.check_batch_max + 1))
    assert client.post("/api/check", json={"ids": too_many}, headers=HEADERS).status_code == 422


def test_status(client: TestClient, settings: Settings, fake: FakeSuwayomi) -> None:
    body = client.get("/api/status").json()
    assert [c.kind for c in fake.calls] == ["ping"]  # one call to Suwayomi, whatever the sources
    assert body["suwayomi_url"] == settings.suwayomi_url
    assert body["reachable"] is True and body["version"] == "v-test"
    assert [s["id"] for s in body["sources"]] == ["z1", "z2"]  # enabled only
    assert body["sources"][0] == {
        "id": "z1",
        "name": "中一",
        "lang": "zh",
        "breaker": "closed",
        "retry_in_s": 0.0,
    }
    assert body["catalog"] == {"built_at": None, "dump_id": None, "works": None}  # not built
    fake.down = True
    assert client.get("/api/status").json()["reachable"] is False


def test_status_tells_the_page_check_limit(client: TestClient, settings: Settings) -> None:
    limit = client.get("/api/status").json()["check_batch_max"]
    assert limit == settings.check_batch_max
    ids = list(range(1, limit + 2))
    assert client.post("/api/check", json={"ids": ids}, headers=HEADERS).status_code == 422


def read(client: TestClient, manga_id: int) -> httpx.Response:
    return client.get(f"/api/read/{manga_id}", follow_redirects=False)


def suwayomi_page(settings: Settings, manga_id: int) -> str:
    return f"{settings.suwayomi_url}/manga/{manga_id}"


def test_read_fetches_the_chapters_an_empty_entry_lacks_then_redirects(
    client: TestClient, settings: Settings, fake: FakeSuwayomi
) -> None:
    fake.states[7] = MangaState("z1", True, 0)
    fake.fetches[7] = 17
    r = read(client, 7)
    assert (r.status_code, r.headers["location"]) == (302, suwayomi_page(settings, 7))
    assert [c.arg for c in fake.of("fetch_chapters")] == ["7"]
    assert read(client, 7).status_code == 302  # opened again: it has its chapters now
    assert len(fake.of("fetch_chapters")) == 1


def test_read_redirects_when_the_fetch_fails(
    client: TestClient, settings: Settings, fake: FakeSuwayomi
) -> None:
    fake.states[7] = MangaState("z1", True, 0)
    fake.fetches[7] = SourceFailure("boom")
    r = read(client, 7)
    assert (r.status_code, r.headers["location"]) == (302, suwayomi_page(settings, 7))
    assert len(fake.of("fetch_chapters")) == 1


def test_read_follows_a_configured_suwayomi_address(settings: Settings, fake: FakeSuwayomi) -> None:
    moved = replace(settings, suwayomi_url="http://suwayomi.test:9/")
    app = create_app(moved)
    app.state.suwayomi_client_factory = lambda _settings: fake
    fake.states[7] = MangaState("z1", False, 0)
    with TestClient(app, base_url=f"http://{moved.host}:{moved.port}") as c:
        assert read(c, 7).headers["location"] == "http://suwayomi.test:9/manga/7"


def app_at(handler: Any) -> httpx.MockTransport:
    """The way to a MangaAtlas app that `handler` plays."""
    return httpx.MockTransport(handler)


def nothing_listening(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def test_warm_needs_the_running_app(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert asyncio.run(warm_async(settings, 5, app_at(nothing_listening))) == 1  # no catalogue
    make_atlas(settings.atlas_db, WORKS)
    assert asyncio.run(warm_async(settings, 5, app_at(nothing_listening))) == 1
    hint = capsys.readouterr().out.splitlines()[-1]
    assert "manga-atlas serve" in hint and "systemctl --user start manga-atlas" in hint


def test_warm_lets_the_app_do_the_checking(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = replace(settings, app_request_timeout_s=42.0)
    make_atlas(settings.atlas_db, WORKS)
    sent: list[list[int]] = []
    read_timeouts: set[float] = set()

    def app(request: httpx.Request) -> httpx.Response:
        read_timeouts.add(request.extensions["timeout"]["read"])
        if request.url.path == "/api/status":
            return httpx.Response(200, json={"reachable": True, "check_batch_max": 1})
        assert request.url.path == "/api/check" and request.headers["x-atlas"] == "1"
        ids = json.loads(request.content)["ids"]
        sent.append(ids)
        lines = [
            {"id": i, "readable": "yes", "done": n, "total": len(ids)} for n, i in enumerate(ids, 1)
        ]
        return httpx.Response(200, text="".join(json.dumps(line) + "\n" for line in lines))

    assert asyncio.run(warm_async(settings, 5, app_at(app))) == 0
    assert sent == [[1], [2]]  # at most check_batch_max works per request
    assert read_timeouts == {42.0}  # status and every progress line wait as configured
    assert "[2/2] 2 yes" in capsys.readouterr().out

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    assert asyncio.run(warm_async(settings, 5, app_at(broken))) == 1
