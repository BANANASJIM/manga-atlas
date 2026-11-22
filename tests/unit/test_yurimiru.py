"""ゆりみる as a tag source: reading a work page; the crawl, offline against a scripted site (robots
kept to, requests spaced, only new or changed pages fetched, failures resumed); and the tags the
build reads back."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from manga_atlas.build import vocab as vocab_rules
from manga_atlas.build.tagsources import common, yurimiru
from manga_atlas.build.tagsources.common import Work
from manga_atlas.config import Settings

HOST = "https://www.yurimiru.com"
ROBOTS = "User-agent: ClaudeBot\nDisallow: /\n\nUser-agent: *\nAllow: /\nDisallow: /api/\n"


def wid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def page(
    title: str,
    medium: str = "漫画",
    tags: tuple[str, ...] = (),
    romance: bool = False,
    scales: dict[str, int] | None = None,
) -> str:
    """A work page in the site's markup (2026-11), cut down to what is read."""
    badge = '<span class="text-xs font-bold" style="color:#d6579e">♡ 恋愛百合</span>'
    chart = '<div><span class="t">百合成分表</span><a href="/seibun">自分も測る →</a></div>'
    bars = "".join(
        f'<div class="flex items-center gap-2"><span class="text-[10px]">{name}</span>'
        f'<div class="flex-1 h-1.5 rounded-full" style="x"><div class="h-1.5" style="width:{v}%">'
        f'</div></div><span class="text-[10px] font-bold">{v}<!-- -->%</span></div>'
        for name, v in (scales or {}).items()
    )
    chips = "".join(f'<a class="xs" href="/works?tags={quote(t)}">#<!-- -->{t}</a>' for t in tags)
    return (
        f"<div>{badge if romance else ''}</div><h1>{title}<span>1<!-- -->巻</span></h1>"
        f"<div>{chart + bars if scales else ''}</div><div>{chips}</div><table><tbody>"
        f'<tr><td class="py-2">作品名</td><td class="py-2 text-gray-700">{title}</td></tr>'
        f'<tr><td class="py-2">ジャンル</td><td class="py-2 text-gray-700">{medium}</td></tr>'
        "</tbody></table>"
    )


def test_small_markup_changes_still_read_and_big_ones_refuse() -> None:
    text = page("D", tags=("学園",), romance=True, scales={"糖度": 60})
    drift = text.replace('">#<!-- -->学園', '" data-x="1">#<!-- -->学園')  # attribute after href
    drift = drift.replace(">♡ 恋愛百合</span>", ">♡ 恋愛百合 </span>")  # space before the close
    assert yurimiru.read_page(drift) | {"scales": {}} == yurimiru.read_page(text) | {"scales": {}}
    assert yurimiru.read_page(drift)["romance"] is True
    with pytest.raises(ValueError, match="chips"):
        yurimiru.read_page(text.replace('href="/works?tags=', "href='/works?tags="))
    with pytest.raises(ValueError, match="百合成分表"):
        yurimiru.read_page(text.replace("60<!-- -->%", "?"))


def test_a_work_page_says_title_medium_tags_mark_and_scales() -> None:
    text = page(
        "A &amp; B", tags=("学園", "連載中"), romance=True, scales={"糖度": 52, "刺激度": 11}
    )
    assert yurimiru.read_page(text) == {
        "title": "A & B",
        "medium": "漫画",
        "tags": ["学園", "連載中"],
        "romance": True,
        "scales": {"糖度": 52, "刺激度": 11},
    }
    assert yurimiru.read_page(page("C"))["romance"] is False
    with pytest.raises(ValueError):
        yurimiru.read_page("<html><p>no work here</p></html>")


# ---- the crawl, against a scripted site ----


class Site:
    """Answers robots.txt, the sitemap and work pages; records every request."""

    def __init__(self) -> None:
        self.robots: tuple[int, str] = (200, ROBOTS)
        self.works: dict[str, tuple[str, str]] = {}  # id -> (lastmod, page)
        self.status: dict[str, int] = {}  # id -> what its page answers instead of 200
        self.busy = 0  # requests to answer 503 first
        self.requests: list[httpx.Request] = []

    def add(self, n: int, lastmod: str, text: str) -> None:
        self.works[wid(n)] = (lastmod, text)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(self.robots[0], text=self.robots[1])
        if self.busy:
            self.busy -= 1
            return httpx.Response(503, headers={"Retry-After": "0"})
        if path == "/sitemap.xml":
            urls = "".join(
                f"<url><loc>{HOST}/works/{i}/vol/1</loc><lastmod>{m}</lastmod></url>"
                for i, (m, _) in self.works.items()
            )
            return httpx.Response(
                200,
                text='<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/'
                f'sitemap/0.9"><url><loc>{HOST}/works</loc></url>{urls}</urlset>',
            )
        m = re.fullmatch(r"/works/([0-9a-f-]+)/vol/1", path)
        if m and m[1] in self.works:
            status = self.status.get(m[1], 200)
            return httpx.Response(status, text=self.works[m[1]][1] if status == 200 else "")
        return httpx.Response(404)

    def pages_asked(self) -> list[str]:
        return [r.url.path.split("/")[2] for r in self.requests if "/vol/" in r.url.path]


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Site:
    fake = Site()
    fake.add(1, "2026-10-01T00:00:00Z", page("やがて君になる", tags=("学園",), romance=True))
    fake.add(2, "2026-11-01T00:00:00Z", page("小説の話", medium="小説"))
    fake.add(3, "2026-09-01T00:00:00Z", page("ウェブの話", medium="Web漫画", tags=("年の差",)))

    def client(settings: Settings, *, follow_redirects: bool = True) -> httpx.Client:
        transport = httpx.MockTransport(fake.handle)
        headers = {"User-Agent": settings.user_agent}
        return httpx.Client(transport=transport, headers=headers, follow_redirects=follow_redirects)

    monkeypatch.setattr(common, "client", client)
    return fake


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, yurimiru_interval_s=0, yurimiru_busy_wait_s=0)


def cache(settings: Settings) -> dict[str, Any]:
    return dict(json.loads((settings.tag_cache("yurimiru") / yurimiru.CACHE).read_text()))


def age(settings: Settings, days: float) -> None:
    path = settings.tag_cache("yurimiru") / yurimiru.CACHE
    data = cache(settings)
    data[common.FETCHED_AT] = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    path.write_text(json.dumps(data))


def test_robots_first_then_the_sitemap_then_each_page(settings: Settings, site: Site) -> None:
    logs: list[str] = []
    yurimiru.refresh(settings, [], logs.append)
    paths = [r.url.path for r in site.requests]
    assert paths[:2] == ["/robots.txt", "/sitemap.xml"]
    assert site.pages_asked() == [wid(2), wid(1), wid(3)]  # the most recently changed first
    assert {r.headers["User-Agent"] for r in site.requests} == {settings.user_agent}
    got = cache(settings)
    assert got[common.FETCHED_AT] != yurimiru.NEVER
    assert got["entries"][wid(1)] == {
        "url": f"{HOST}/works/{wid(1)}/vol/1",
        "lastmod": "2026-10-01T00:00:00Z",
        "parser": yurimiru.PARSER,
        "title": "やがて君になる",
        "medium": "漫画",
        "tags": ["学園"],
        "romance": True,
        "scales": {},
    }
    assert got["entries"][wid(2)]["medium"] == "小説"  # kept, so not fetched again
    assert any("3 pages fetched" in line for line in logs)


def test_what_robots_forbids_is_never_asked(settings: Settings, site: Site) -> None:
    site.robots = (200, "User-agent: *\nDisallow: /works/\n")
    yurimiru.refresh(settings, [], print)
    assert site.pages_asked() == []

    site.requests.clear()
    site.robots = (403, "")  # refused: nothing may be fetched, the sitemap included
    (settings.tag_cache("yurimiru") / yurimiru.CACHE).unlink()
    logs: list[str] = []
    yurimiru.refresh(settings, [], logs.append)
    assert [r.url.path for r in site.requests] == ["/robots.txt"]
    assert any("fetch failed" in line for line in logs)
    assert not (settings.tag_cache("yurimiru") / yurimiru.CACHE).exists()

    site.robots = (404, "")  # none: everything may
    yurimiru.refresh(settings, [], print)
    assert len(site.pages_asked()) == 3


def test_requests_are_spaced_as_configured_or_as_robots_ask(
    tmp_path: Path, site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(common.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(common.time, "sleep", sleep)
    spaced = Settings(data_dir=tmp_path, yurimiru_interval_s=3.0)
    yurimiru.refresh(spaced, [], print)
    assert waits == [3.0] * (len(site.requests) - 1)

    waits.clear()
    site.requests.clear()
    site.robots = (200, ROBOTS + "Crawl-delay: 5\n")
    age(spaced, 40)
    site.works[wid(1)] = ("2026-11-20T00:00:00Z", site.works[wid(1)][1])
    yurimiru.refresh(spaced, [], print)
    assert waits == [5.0] * (len(site.requests) - 1)  # robots asked for longer


def test_only_the_sites_own_work_pages_are_asked(settings: Settings, site: Site) -> None:
    site.add(9, "2026-11-30T00:00:00Z", page("よその話"))
    original = site.handle

    def elsewhere(request: httpx.Request) -> httpx.Response:
        response = original(request)
        if request.url.path == "/sitemap.xml":  # one entry points at another host
            text = response.text.replace(
                f"{HOST}/works/{wid(9)}", f"https://other.test/works/{wid(9)}"
            )
            return httpx.Response(200, text=text)
        return response

    site.handle = elsewhere  # type: ignore[method-assign]
    yurimiru.refresh(settings, [], print)
    assert {r.url.host for r in site.requests} == {"www.yurimiru.com"}
    assert wid(9) not in cache(settings)["entries"]


def redirect(site: Site, n: int, location: str) -> None:
    """Work n's page answers with a redirect to `location`."""
    original = site.handle

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/works/{wid(n)}/vol/1" and not request.url.query:
            site.requests.append(request)
            return httpx.Response(302, headers={"Location": location})
        return original(request)

    site.handle = handle  # type: ignore[method-assign]


@pytest.mark.parametrize(
    "location", ["/api/works/1", "https://other.test/api/works/1", "http://www.yurimiru.com/x"]
)
def test_a_redirect_off_the_site_or_into_what_robots_forbid_is_not_followed(
    settings: Settings, site: Site, location: str
) -> None:
    redirect(site, 1, location)
    logs: list[str] = []
    yurimiru.refresh(settings, [], logs.append)
    assert all(
        r.url.path != "/api/works/1" and r.url.host == "www.yurimiru.com" for r in site.requests
    )
    assert all(r.url.scheme == "https" for r in site.requests)
    assert wid(1) not in cache(settings)["entries"] and any("not followed" in x for x in logs)


def test_a_redirect_within_the_site_is_followed_and_spaced(
    tmp_path: Path, site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(common.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(common.time, "sleep", sleep)
    redirect(site, 1, f"/works/{wid(1)}/vol/1?moved=1")
    spaced = Settings(data_dir=tmp_path, yurimiru_interval_s=3.0)
    yurimiru.refresh(spaced, [], print)
    assert cache(spaced)["entries"][wid(1)]["title"] == "やがて君になる"
    assert len(site.requests) == 6 and waits == [3.0] * 5  # the hop is spaced too

    redirect(site, 3, f"/works/{wid(3)}/vol/1")  # to itself, for ever
    looping = Settings(data_dir=tmp_path / "loop", yurimiru_interval_s=0)
    logs: list[str] = []
    yurimiru.refresh(looping, [], logs.append)
    assert any("redirects" in x for x in logs) and wid(3) not in cache(looping)["entries"]


@pytest.mark.parametrize("status", [429, 500, 503, 400])
def test_robots_txt_that_cannot_be_read_ends_the_refresh(
    settings: Settings, site: Site, status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(yurimiru.time, "sleep", lambda s: None)
    site.robots = (status, "")
    logs: list[str] = []
    yurimiru.refresh(settings, [], logs.append)
    assert {r.url.path for r in site.requests} == {"/robots.txt"}  # nothing else asked
    assert any("fetch failed" in x for x in logs)
    assert not (settings.tag_cache("yurimiru") / yurimiru.CACHE).exists()


@pytest.mark.parametrize("status", [404, 410])
def test_no_robots_txt_lets_everything_be_fetched(
    settings: Settings, site: Site, status: int
) -> None:
    site.robots = (status, "")
    yurimiru.refresh(settings, [], print)
    assert len(site.pages_asked()) == 3


def test_a_page_that_no_longer_reads_keeps_its_entry_and_is_tried_again(
    settings: Settings, site: Site
) -> None:
    yurimiru.refresh(settings, [], print)
    before = cache(settings)["entries"][wid(1)]
    age(settings, 40)
    changed = page("やがて君になる", tags=("学園",), scales={"糖度": 70})
    site.works[wid(1)] = ("2026-11-18T00:00:00Z", changed.replace("70<!-- -->%", "?"))
    site.works[wid(3)] = ("2026-11-17T00:00:00Z", page("ウェブの話", medium="Web漫画"))
    yurimiru.refresh(settings, [], print)
    got = cache(settings)
    assert got["entries"][wid(1)] == before  # not overwritten, its old lastmod kept
    assert got[common.FETCHED_AT] != yurimiru.NEVER and not common.fresh(got, 30)

    site.works[wid(1)] = ("2026-11-18T00:00:00Z", changed)
    site.requests.clear()
    yurimiru.refresh(settings, [], print)
    assert site.pages_asked() == [wid(1)]
    assert cache(settings)["entries"][wid(1)]["scales"] == {"糖度": 70}
    assert common.fresh(cache(settings), 30)


def test_entries_read_by_another_parser_are_fetched_again(settings: Settings, site: Site) -> None:
    yurimiru.refresh(settings, [], print)  # fresh now
    path = settings.tag_cache("yurimiru") / yurimiru.CACHE
    data = cache(settings)
    data["entries"][wid(2)]["parser"] = yurimiru.PARSER - 1
    path.write_text(json.dumps(data))
    site.requests.clear()
    yurimiru.refresh(settings, [], print)
    assert site.pages_asked() == [wid(2)]  # though its lastmod is the same and the cache fresh
    assert cache(settings)["entries"][wid(2)]["parser"] == yurimiru.PARSER


def test_only_new_or_changed_pages_are_fetched_again(settings: Settings, site: Site) -> None:
    yurimiru.refresh(settings, [], print)
    site.requests.clear()
    yurimiru.refresh(settings, [], print)
    assert site.requests == []  # fresh: nothing asked at all

    age(settings, 31)
    site.works[wid(1)] = ("2026-11-15T00:00:00Z", page("やがて君になる", tags=("学園", "先輩後輩")))
    site.add(4, "2026-11-10T00:00:00Z", page("新しい話"))
    del site.works[wid(3)]
    yurimiru.refresh(settings, [], print)
    assert site.pages_asked() == [wid(1), wid(4)]
    entries = cache(settings)["entries"]
    assert set(entries) == {wid(1), wid(2), wid(4)}  # 3 left the site
    assert entries[wid(1)]["tags"] == ["学園", "先輩後輩"]


def test_a_failure_keeps_what_was_fetched_and_the_next_run_goes_on(
    settings: Settings, site: Site
) -> None:
    site.status[wid(1)] = 500  # the second page asked
    logs: list[str] = []
    yurimiru.refresh(settings, [], logs.append)
    assert any("fetch failed, 1 of 3 read" in line for line in logs)
    got = cache(settings)
    assert got[common.FETCHED_AT] == yurimiru.NEVER  # stale: the next build goes on
    assert set(got["entries"]) == {wid(2)}

    del site.status[wid(1)]
    site.requests.clear()
    yurimiru.refresh(settings, [], print)
    assert site.pages_asked() == [wid(1), wid(3)]
    assert cache(settings)[common.FETCHED_AT] != yurimiru.NEVER


def test_a_failure_before_any_page_leaves_the_cache_alone(settings: Settings, site: Site) -> None:
    yurimiru.refresh(settings, [], print)
    age(settings, 40)
    path = settings.tag_cache("yurimiru") / yurimiru.CACHE
    before = path.read_bytes()
    site.works.clear()  # a sitemap of another shape: no work pages in it
    logs: list[str] = []
    yurimiru.refresh(settings, [], logs.append)
    assert path.read_bytes() == before
    assert any("lists no work pages" in line for line in logs)


def test_a_busy_site_is_waited_for(
    settings: Settings, site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []
    monkeypatch.setattr(yurimiru.time, "sleep", waits.append)
    site.busy = 2
    yurimiru.refresh(settings, [], print)
    assert waits.count(0.0) >= 2 and len(cache(settings)["entries"]) == 3


def test_gone_or_unreadable_pages_are_left_for_next_time(settings: Settings, site: Site) -> None:
    site.status[wid(2)] = 404
    site.works[wid(3)] = (site.works[wid(3)][0], "<html>maintenance</html>")
    logs: list[str] = []
    yurimiru.refresh(settings, [], logs.append)
    assert set(cache(settings)["entries"]) == {wid(1)}
    assert any("1 to read again" in line for line in logs)
    assert cache(settings)[common.FETCHED_AT] == yurimiru.NEVER  # stale until it is read

    for i in site.works:  # the site changed its pages: nothing reads, nothing is kept
        site.works[i] = ("2026-11-19T00:00:00Z", "<html>maintenance</html>")
    age(settings, 40)
    logs.clear()
    yurimiru.refresh(settings, [], logs.append)
    assert any("has the site changed" in line for line in logs)
    assert set(cache(settings)["entries"]) == {wid(1)}


def test_a_capped_refresh_takes_the_newest_and_stays_stale(tmp_path: Path, site: Site) -> None:
    trial = Settings(data_dir=tmp_path, yurimiru_interval_s=0, yurimiru_max_pages=1)
    yurimiru.refresh(trial, [], print)
    assert site.pages_asked() == [wid(2)]
    assert cache(trial)[common.FETCHED_AT] == yurimiru.NEVER
    yurimiru.refresh(trial, [], print)
    assert site.pages_asked() == [wid(2), wid(1)]


def test_disabled_and_offline_never_fail(tmp_path: Path) -> None:
    logs: list[str] = []
    settings = Settings(data_dir=tmp_path)  # the session's transport refuses every request
    yurimiru.refresh(settings, [], logs.append)
    assert yurimiru.pages(settings, [], vocab_rules.load(), logs.append) == {}
    assert not settings.tag_cache("yurimiru").exists()
    assert any("fetch failed" in line for line in logs)
    assert not yurimiru.enabled(Settings(data_dir=tmp_path, yurimiru_enabled=False))


# ---- what the build reads back ----


def entry(title: str, medium: str = "漫画", **kw: Any) -> dict[str, Any]:
    url = f"{HOST}/works/{quote(title)}/vol/1"
    facts = {"tags": [], "romance": False, "scales": {}} | kw
    return {
        "url": url,
        "lastmod": "2026-11-01T00:00:00Z",
        "title": title,
        "medium": medium,
        **facts,
    }


def work(n: int, name: str, *aliases: str) -> Work:
    return Work(n, name, aliases, 0)


def built(settings: Settings, *entries: dict[str, Any]) -> dict[int, Any]:
    by_id = {wid(i): e for i, e in enumerate(entries, start=1)}
    common.write(
        settings.tag_cache("yurimiru") / yurimiru.CACHE,
        {"entries": by_id},
    )
    names = [
        "やがて君になる", "小説の話", "ウェブの話", "あまくない",
        "巻つき", "同名作", "あお", "あお",
    ]  # fmt: skip
    works = [work(n, name) for n, name in enumerate(names, start=1)]
    return yurimiru.pages(settings, works, vocab_rules.load(), print)


def test_manga_entries_are_matched_and_their_tags_translated(settings: Settings) -> None:
    got = built(
        settings,
        entry("やがて君になる", tags=["学園", "先輩後輩", "連載中"], romance=True),
        entry("小説の話", medium="小説", tags=["学園"]),  # not manga: never matched
        entry("ウェブの話", medium="Web漫画", tags=["年の差"], scales={"糖度": 60}),
        entry("あまくない", tags=["日常"], scales={"糖度": 30}),
        entry("巻つき 2巻", tags=["ホラー"]),  # the volume number goes before matching
        entry("同名作", tags=["日常"]),  # two entries by one title: nothing tells them apart
        entry("同名作", tags=["ホラー"]),
        entry("あお", tags=["日常"]),  # two works by this name: the homonym guard
    )
    assert {w: p.tags for w, p in got.items()} == {
        1: ("校园", "前后辈", "百合"),  # 連載中 is no tag; 百合 for the 恋愛百合 mark
        3: ("年龄差", "百合"),  # sweet enough
        4: ("日常",),  # neither marked nor sweet enough
        5: ("恐怖",),
    }
    assert got[5].title == "巻つき" and got[1].url.startswith(f"{HOST}/works/")


def test_an_unusable_cache_gives_no_tags(settings: Settings) -> None:
    path = settings.tag_cache("yurimiru") / yurimiru.CACHE
    common.write(path, {"entries": {"x": {"title": "t"}}})
    logs: list[str] = []
    assert yurimiru.pages(settings, [], vocab_rules.load(), logs.append) == {}
    assert any("unusable" in line for line in logs)


def test_every_site_tag_has_a_name_here() -> None:
    names = vocab_rules.load().source_rules["yurimiru"]["tags"]
    assert len(names) == 67 + 1 and all(isinstance(v, str) and v for v in names.values())
    assert names["短編集"] == "短篇集"  # the one chip beyond /tags kept, as Bangumi has the tag
