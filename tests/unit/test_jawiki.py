"""Japanese Wikipedia as a tag source: category rules, the refresh (offline, through a scripted
transport), and what the build reads back from the cache."""

from __future__ import annotations

import gzip
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from manga_atlas.build import vocab as vocab_rules
from manga_atlas.build.tagsources import common, jawiki
from manga_atlas.build.tagsources.common import Work
from manga_atlas.config import Settings

RULES = jawiki.Rules(vocab_rules.load().source_rules["jawiki"])
TITLES = ["page_title", "カラダ探し", "シカバネ", "シカバネ_(漫画)", "同名", "Gate", "Akira"]


@pytest.mark.parametrize(
    ("category", "tag"),
    [
        ("ホラー漫画", "ホラー"),
        ("SF漫画作品", "SF"),
        ("4コマ漫画", "4コマ"),
        ("デスゲームを題材とした漫画作品", "デスゲーム"),
        ("悪魔を題材とした作品", "悪魔"),
        ("高等学校を舞台とした漫画作品", "高等学校（舞台）"),
        ("仙台市を舞台とした作品", "仙台市（舞台）"),
        ("週刊少年ジャンプの漫画作品", None),  # a magazine's
        ("伊藤潤二の漫画作品", None),  # an author's
        ("2014年の漫画", None),
        ("漫画作品 か", None),  # the index
        ("高等学校を舞台としたアニメ作品", None),  # another medium
        ("2019年のテレビアニメ", None),
        ("日本の漫画家", None),
        ("継続中の作品", None),
        ("探偵を主人公とした漫画作品", "探偵"),
        ("聴覚障害を扱った漫画", "聴覚障害"),
        ("魔界を舞台にした作品", "魔界（舞台）"),
        ("日本の高校野球を題材とした漫画作品", "野球"),  # the sport's
        ("日本の小説を原作とする漫画作品", "日本の小説原作"),
        ("文明崩壊後の世界が描かれた漫画作品", "文明崩壊後"),
        ("ダーク・ファンタジー", "ダーク・ファンタジー"),
        ("日本を舞台とした漫画作品", None),  # nearly every work's
    ],
)
def test_categories_become_tags_by_the_vocabulary_rules(category: str, tag: str | None) -> None:
    assert RULES.tag(category) == tag


def test_only_manga_articles_that_are_not_disambiguation_pages_count() -> None:
    assert RULES.accepts(["ホラー漫画", "2014年の漫画"])
    assert RULES.accepts(["漫画作品 か"])
    assert not RULES.accepts(["日本の漫画家", "存命人物"])  # a person
    assert not RULES.accepts(["日本の楽曲"])
    assert not RULES.accepts(["漫画作品 し", "曖昧さ回避"])
    assert RULES.tags(["ホラー漫画", "ホラー漫画", "2014年の漫画", "悪魔を題材とした作品"]) == (
        "ホラー",
        "悪魔",
    )


# ---- refresh, against a scripted Wikidata, dump server and MediaWiki API ----


def works() -> list[Work]:
    return [
        Work(1, "カラダ探し", ("寻找身体",), 300),
        Work(2, "シカバネ", (), 200),  # the article is "シカバネ (漫画)"
        Work(3, "同名", (), 50),  # two works by one name: no title match
        Work(4, "同名", (), 40),
        Work(5, "ゲート", (), 100),  # Wikidata links it to "Gate"
        Work(6, "アキラ", ("Akira",), 10),  # an alias without kana is not tried
    ]


class Wiki:
    """Scripted answers; records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.lagging = 0  # API requests to refuse with maxlag first
        self.busy = 0  # API requests to refuse with 429 first
        self.busy_after: str | None = "0"  # their Retry-After header, if any
        self.categories: dict[str, list[str]] = {
            "カラダ探し": ["ホラー漫画", "漫画作品 か", "高等学校を舞台とした漫画作品"],
            "シカバネ (漫画)": ["2010年の漫画", "ゾンビを題材とした漫画作品"],
            "Gate (小説)": ["ファンタジー漫画", "漫画作品 け"],
        }
        self.redirects = {"Gate": "Gate (小説)"}
        self.titles = list(TITLES)
        self.links = {"5": "Gate", "99999": "他所"}  # Wikidata's Bangumi links; 99999 is no work
        self.fail_api = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith(".gz"):
            return httpx.Response(200, content=gzip.compress("\n".join(self.titles).encode()))
        if request.url.path == "/sparql":
            rows = [{"bangumi": {"value": b}, "title": {"value": t}} for b, t in self.links.items()]
            return httpx.Response(200, json={"results": {"bindings": rows}})
        if self.fail_api:
            return httpx.Response(503)
        if self.busy:
            self.busy -= 1
            headers = {} if self.busy_after is None else {"Retry-After": self.busy_after}
            return httpx.Response(429, headers=headers)
        if self.lagging:
            self.lagging -= 1
            lag = {"error": {"code": "maxlag", "info": "lagged"}}
            return httpx.Response(200, json=lag, headers={"Retry-After": "0"})
        return httpx.Response(200, json=self.query(form(request)))

    def query(self, params: dict[str, list[str]]) -> dict[str, Any]:
        """One category per page per answer, so every page needs continuing."""
        asked = params["titles"][0].split("|")
        offset = int(params.get("clcontinue", ["0"])[0])
        redirects = [{"from": t, "to": self.redirects[t]} for t in asked if t in self.redirects]
        pages, more = [], False
        for title in asked:
            title = self.redirects.get(title, title)
            if title not in self.categories:
                pages.append({"ns": 0, "title": title, "missing": True})
                continue
            cats = self.categories[title][offset : offset + 1]
            more = more or offset + 1 < len(self.categories[title])
            pages.append({"ns": 0, "title": title, "categories": [
                {"ns": 14, "title": f"Category:{c}"} for c in cats
            ]})  # fmt: skip
        out: dict[str, Any] = {"query": {"redirects": redirects, "pages": pages}}
        if more:
            out["continue"] = {"clcontinue": str(offset + 1), "continue": "||"}
        return out


@pytest.fixture
def wiki(monkeypatch: pytest.MonkeyPatch) -> Wiki:
    fake = Wiki()

    def client(settings: Settings, *, follow_redirects: bool = True) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(fake.handle),
            headers={"User-Agent": settings.user_agent},
            follow_redirects=follow_redirects,
        )

    monkeypatch.setattr(common, "client", client)
    return fake


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, jawiki_interval_s=0, jawiki_batch=2, jawiki_busy_wait_s=0)


def cache(settings: Settings) -> dict[str, Any]:
    return dict(json.loads((settings.tag_cache("jawiki") / jawiki.CACHE).read_text()))


def api_calls(wiki: Wiki) -> list[httpx.Request]:
    return [r for r in wiki.requests if r.url.path == "/w/api.php"]


def form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode())


def test_refresh_matches_fetches_and_caches(settings: Settings, wiki: Wiki) -> None:
    logs: list[str] = []
    jawiki.refresh(settings, works(), logs.append)
    got = cache(settings)
    assert got["works"] == {
        "1": {"title": "カラダ探し", "article": "カラダ探し", "linked": False},
        "2": {"title": "シカバネ (漫画)", "article": "シカバネ (漫画)", "linked": False},
        "5": {"title": "Gate", "article": "Gate (小説)", "linked": True},  # through a redirect
    }
    assert got["categories"]["シカバネ (漫画)"] == ["2010年の漫画", "ゾンビを題材とした漫画作品"]
    assert set(got["categories"]) == {"カラダ探し", "シカバネ (漫画)", "Gate (小説)"}
    assert any("3 works lead to an article" in line for line in logs)

    calls = api_calls(wiki)
    assert {c.method for c in calls} == {"POST"}  # 50 long titles may not fit in a URL
    first = form(calls[0])
    assert first["maxlag"] == [str(settings.jawiki_maxlag_s)] and first["redirects"] == ["1"]
    assert first["clshow"] == ["!hidden"] and first["cllimit"] == ["max"]
    assert all(len(form(c)["titles"][0].split("|")) <= 2 for c in calls)
    assert {r.headers["User-Agent"] for r in wiki.requests} == {settings.user_agent}
    sparql = next(r for r in wiki.requests if r.url.path == "/sparql")
    assert "P5732" in parse_qs(sparql.url.query.decode())["query"][0]


def test_who_keeps_an_article_is_decided_when_building(settings: Settings, wiki: Wiki) -> None:
    sequels = {"カラダ探し 解": "カラダ探し", "カラダ探し 異": "カラダ探し"}
    wiki.titles += ["ゲートの話", "扉", "とびら", *sequels]
    wiki.redirects |= {"ゲートの話": "Gate (小説)", "扉": "扉の物語", "とびら": "扉の物語"}
    wiki.redirects |= sequels
    wiki.categories["扉の物語"] = ["ファンタジー漫画"]
    more = [
        Work(7, "ゲートの話", (), 30),  # leads to 5's article, which Wikidata gives 5
        Work(8, "扉", (), 20),  # 8 and 9: two redirects to one article
        Work(9, "とびら", (), 20),
        Work(10, "カラダ探し 解", (), 20),  # sequels whose titles redirect to the original's
        Work(11, "カラダ探し 異", (), 20),
    ]
    jawiki.refresh(settings, works() + more, print)
    got = cache(settings)
    assert set(got["works"]) == {"1", "2", "5", "7", "8", "9", "10", "11"}  # all, as fetched
    sequel = {"title": "カラダ探し 解", "article": "カラダ探し", "linked": False}
    assert got["works"]["10"] == sequel
    assert "扉の物語" in got["categories"]
    assert sorted(jawiki.pages(settings, [], vocab_rules.load(), print)) == [1, 2, 5]


def test_a_fresh_cache_is_not_fetched_again(settings: Settings, wiki: Wiki) -> None:
    jawiki.refresh(settings, works(), print)
    asked = len(wiki.requests)
    jawiki.refresh(settings, works(), print)
    assert len(wiki.requests) == asked

    path = settings.tag_cache("jawiki") / jawiki.CACHE
    old = cache(settings)
    old["fetched_at"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    path.write_text(json.dumps(old))
    jawiki.refresh(settings, works(), print)
    assert len(wiki.requests) > asked  # too old: fetched anew


def test_a_failed_refresh_keeps_the_old_cache(settings: Settings, wiki: Wiki) -> None:
    path = settings.tag_cache("jawiki") / jawiki.CACHE
    common.write(path, {"works": {"1": "カラダ探し"}, "categories": {"カラダ探し": ["ホラー漫画"]}})
    stale = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    path.write_text(json.dumps({**cache(settings), "fetched_at": stale}))
    before = path.read_bytes()
    wiki.fail_api = True
    logs: list[str] = []
    jawiki.refresh(settings, works(), logs.append)
    assert path.read_bytes() == before
    assert any("fetch failed" in line for line in logs)


def test_lagging_replicas_are_waited_for(
    settings: Settings, wiki: Wiki, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []
    monkeypatch.setattr(jawiki.time, "sleep", waits.append)
    wiki.lagging = 2
    jawiki.refresh(settings, works(), print)
    assert cache(settings)["works"]["1"]["article"] == "カラダ探し"
    assert waits.count(0.0) == 2  # as long as Retry-After said, twice

    wiki.lagging = settings.jawiki_lag_retries + 1  # lagging for good: give up, keep nothing
    (settings.tag_cache("jawiki") / jawiki.CACHE).unlink()
    jawiki.refresh(settings, works(), print)
    assert not (settings.tag_cache("jawiki") / jawiki.CACHE).exists()


def test_a_busy_api_is_waited_for(
    settings: Settings, wiki: Wiki, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []
    monkeypatch.setattr(jawiki.time, "sleep", waits.append)
    patient = Settings(data_dir=settings.data_dir, jawiki_interval_s=0, jawiki_busy_wait_s=7)
    wiki.busy, wiki.busy_after = 2, None  # no Retry-After: back off longer each time
    jawiki.refresh(patient, works(), print)
    assert cache(settings)["works"]["1"]["article"] == "カラダ探し"
    assert [w for w in waits if w >= 7] == [7, 14]

    wiki.busy, wiki.busy_after = settings.jawiki_lag_retries + 1, "0"  # busy for good: give up
    (settings.tag_cache("jawiki") / jawiki.CACHE).unlink()
    jawiki.refresh(settings, works(), print)
    assert not (settings.tag_cache("jawiki") / jawiki.CACHE).exists()


def test_requests_are_spaced(
    settings: Settings, wiki: Wiki, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(jawiki.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(jawiki.time, "sleep", sleep)
    spaced = Settings(data_dir=settings.data_dir, jawiki_interval_s=1.5, jawiki_batch=2)
    jawiki.refresh(spaced, works(), print)
    assert len(waits) == len(api_calls(wiki)) - 1 and set(waits) == {1.5}


def test_a_trial_run_fetches_the_most_collected_first(settings: Settings, wiki: Wiki) -> None:
    trial = Settings(data_dir=settings.data_dir, jawiki_interval_s=0, jawiki_max_pages=2)
    jawiki.refresh(trial, works(), print)
    assert list(cache(trial)["works"]) == ["1", "2"]


def test_disabled_offline_and_failing_sources_never_fail(tmp_path: Path) -> None:
    logs: list[str] = []
    settings = Settings(data_dir=tmp_path)  # the session's transport refuses every request
    jawiki.refresh(settings, works(), logs.append)
    assert jawiki.pages(settings, [], vocab_rules.load(), logs.append) == {}
    assert not settings.tag_cache("jawiki").exists()
    assert any("fetch failed" in line for line in logs)


# ---- what the build reads back ----


def write_cache(settings: Settings, data: dict[str, Any], age_days: float = 0) -> None:
    path = settings.tag_cache("jawiki") / jawiki.CACHE
    common.write(path, data)
    if age_days:
        stamped = {**json.loads(path.read_text())}
        stamped["fetched_at"] = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
        path.write_text(json.dumps(stamped))


def reached(*titles: str) -> dict[str, dict[str, Any]]:
    """Cache entries for works 1, 2, … each reaching its title directly."""
    return {
        str(i): {"title": t, "article": t, "linked": False} for i, t in enumerate(titles, start=1)
    }


def test_pages_keep_manga_articles_and_their_tags(settings: Settings) -> None:
    write_cache(
        settings,
        {
            "works": reached("カラダ探し", "同名", "歌", "消えた"),
            "categories": {
                "カラダ探し": ["ホラー漫画", "漫画作品 か", "高等学校を舞台とした漫画作品"],
                "同名": ["漫画作品 と", "曖昧さ回避"],
                "歌": ["日本の楽曲"],
            },
        },
    )
    got = jawiki.pages(settings, [], vocab_rules.load(), print)
    assert list(got) == [1]
    assert got[1].tags == ("ホラー", "高等学校（舞台）")
    assert (
        got[1].url == "https://ja.wikipedia.org/wiki/%E3%82%AB%E3%83%A9%E3%83%80%E6%8E%A2%E3%81%97"
    )


@pytest.mark.parametrize(
    "broken",
    [
        b"not json",
        b'{"works": {}}',  # no fetched_at
        b'{"fetched_at": "2026-11-01T00:00:00+00:00", "works": {"1": "x"}}',  # no categories
        b'{"fetched_at": "2026-11-01T00:00:00+00:00", "works": [1]}',
        b'{"fetched_at": "2026-11-01T00:00:00+00:00", "works": {"1": {"title": "x"}},'
        b' "categories": {}}',  # no article
    ],
)
def test_an_unusable_cache_gives_no_tags(settings: Settings, broken: bytes) -> None:
    path = settings.tag_cache("jawiki") / jawiki.CACHE
    path.parent.mkdir(parents=True)
    path.write_bytes(broken)
    logs: list[str] = []
    assert jawiki.pages(settings, [], vocab_rules.load(), logs.append) == {}
    assert logs


def test_article_urls_use_underscores_and_escapes(settings: Settings) -> None:
    assert jawiki.article_url(settings, "Gate (小説)") == (
        "https://ja.wikipedia.org/wiki/Gate_%28%E5%B0%8F%E8%AA%AC%29"
    )


def test_the_cache_is_replaced_whole(settings: Settings, wiki: Wiki) -> None:
    jawiki.refresh(settings, works(), print)
    folder = settings.tag_cache("jawiki")
    assert sorted(os.listdir(folder)) == [jawiki.CACHE, jawiki.TITLES]  # no .part left behind
