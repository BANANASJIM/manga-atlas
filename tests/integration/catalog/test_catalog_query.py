"""Queries over the fixture catalogue: search, facets, the read filter, suggest, work detail."""

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from manga_atlas.availability import state
from manga_atlas.build.people import standin_id
from manga_atlas.config import Settings
from manga_atlas.query.db import open_catalog
from manga_atlas.query.search import Query, parse_years, search
from manga_atlas.query.suggest import suggest
from manga_atlas.query.work import work
from manga_atlas.text import key


def find(settings: Settings, **kw: Any) -> dict[str, Any]:
    return search(open_catalog(settings), settings, Query(**kw))


def ids(settings: Settings, **kw: Any) -> list[int]:
    return [item["id"] for item in find(settings, **kw)["items"]]


def ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


def write_state(settings: Settings, rows: list[tuple[Any, ...]] | None) -> None:
    con = sqlite3.connect(settings.state_db)
    con.execute("PRAGMA journal_mode = WAL")  # as a writer living next to readers would
    if rows is None:  # the app has created the file but not the table yet
        con.execute("CREATE TABLE library(manga_id INTEGER)")
    else:
        con.executescript(state.SCHEMA)
        con.executemany(
            "INSERT INTO availability(work_id, source_id, state, manga_id, title, author_match,"
            " checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    con.commit()
    con.close()


def test_browse_everything_by_popularity(settings: Settings) -> None:
    result = find(settings)
    assert result["total"] == 9
    assert [i["id"] for i in result["items"]][:3] == [101, 104, 103]
    assert {f["name"]: f["n"] for f in result["facets"]["region"]} == {"日本": 5, "韩国": 2}
    assert "tag_header" not in result


def test_tags_and_and_not(settings: Settings) -> None:
    result = find(settings, include=("百合", "校园"), exclude=("恐怖",))
    assert [i["id"] for i in result["items"]] == [101]
    assert result["facets"]["included"] == [
        {"query": "百合", "name": "百合", "grp": "genre"},
        {"query": "校园", "name": "校园", "grp": "genre"},
    ]
    assert result["facets"]["excluded"] == [
        {"query": "恐怖", "name": "恐怖", "grp": "genre", "n": 1}
    ]
    assert ids(settings, include=("GL",)) == [101, 104]  # folded spelling
    assert ids(settings, include=("戀愛",)) == [101, 102]
    assert ids(settings, include=("百合", "恐怖")) == [104]
    assert ids(settings, include=("没有这个标签",)) == []
    assert len(ids(settings, exclude=("没有这个标签",))) == 9


def test_votes_needed_apply_at_query_time(settings: Settings) -> None:
    assert ids(settings, include=("校园",)) == [101, 104, 102]
    strict = Settings(data_dir=settings.data_dir, tag_min_votes=4)
    assert ids(strict, include=("校园",)) == [101, 102]  # 104 has 3 votes; 101 is official


def test_exclusion_only_takes_works_from_everything(settings: Settings) -> None:
    result = find(settings, exclude=("恐怖",))
    assert result["total"] == 7
    assert [i["id"] for i in result["items"]][:3] == [101, 102, 108]
    assert result["facets"]["excluded"] == [
        {"query": "恐怖", "name": "恐怖", "grp": "genre", "n": 2}
    ]
    tags = {f["name"]: f["n"] for f in result["facets"]["tags"]}
    assert tags == {"校园": 2, "恋爱": 2, "百合": 1}
    assert {f["name"]: f["n"] for f in result["facets"]["region"]} == {"日本": 3, "韩国": 2}


def test_broad_filters_count_what_they_drop(settings: Settings) -> None:
    # 7 of 9 works are from 2000 on; 恋爱 removes two of them
    result = find(settings, year_from=2000, exclude=("恋爱",))
    assert result["total"] == 5
    assert [i["id"] for i in result["items"]] == [104, 103, 108, 110, 204]
    assert result["facets"]["excluded"] == [
        {"query": "恋爱", "name": "恋爱", "grp": "genre", "n": 2}
    ]
    tags = {f["name"]: f["n"] for f in result["facets"]["tags"]}
    assert tags == {"恐怖": 2, "惊悚": 1, "百合": 1, "校园": 1}
    assert {f["name"]: f["n"] for f in result["facets"]["region"]} == {"日本": 3, "韩国": 1}


def test_filters(settings: Settings) -> None:
    assert sorted(ids(settings, year_from=2010, year_to=2019)) == [101, 102, 104, 108, 204]
    assert sorted(ids(settings, year_to=2008)) == [103, 110, 112]
    lo, hi = parse_years("2015")
    assert ids(settings, year_from=lo, year_to=hi) == [101]
    assert sorted(ids(settings, statuses=("已完结",))) == [101, 102, 108]
    assert sorted(ids(settings, statuses=("完结",))) == [101, 102, 108]
    assert ids(settings, statuses=("连载中",)) == [103]
    assert ids(settings, statuses=("不存在",)) == []
    assert sorted(ids(settings, regions=("韩国",))) == [102, 110]
    assert sorted(ids(settings, regions=("韓國",))) == [102, 110]
    assert sorted(ids(settings, min_score=8)) == [101, 104]
    assert sorted(ids(settings, people=(61,))) == [104, 111]
    assert ids(settings, people=(61, 62)) == [111]
    standin = standin_id(key("新人作家"))  # an unlinked infobox credit
    assert ids(settings, people=(standin,)) == [110, 112]


def test_sorts(settings: Settings) -> None:
    assert ids(settings, sort="heat") == [101, 104, 103, 102, 108, 110, 111, 204, 112]
    assert ids(settings, sort="rank")[:4] == [101, 103, 104, 102]
    # 104 scores highest, but on 3 votes it sorts after works with enough votes
    assert ids(settings, sort="score")[:4] == [101, 103, 104, 102]
    assert ids(settings, sort="date") == [104, 102, 101, 204, 108, 103, 110, 111, 112]
    assert ids(settings, sort="heat", offset=2, limit=2) == [103, 102]
    assert ids(settings, include=("校园",), sort="score") == [101, 104, 102]


def test_text_search_folds_scripts_and_matches_every_term(settings: Settings) -> None:
    assert ids(settings, text="终将成为你") == [101]
    assert ids(settings, text="終將成為") == [101]  # Traditional input
    assert ids(settings, text="bloom into") == [101]  # alias, any case
    assert ids(settings, text="浦泽直树") == [108, 110]  # people column
    assert ids(settings, text="浦沢") == [108, 110]  # 2 characters: LIKE scan
    assert ids(settings, text="怪") == [104]  # 1 character
    assert ids(settings, text="百合 怪谈") == [104]  # every term must match
    assert ids(settings, text="尸鬼 漫画版") == [103]
    assert ids(settings, text="漫画") == [103, 111]  # inside titles: by popularity


def test_tag_header_only_for_one_tag_without_text(settings: Settings) -> None:
    header = find(settings, include=("校园",))["tag_header"]
    assert header["tag"] == {"name": "校园", "grp": "genre", "n": 3}
    assert {t["name"] for t in header["related"]} == {"百合", "恋爱", "恐怖"}
    assert [t["name"] for t in header["related"]][2] == "恐怖"  # weakest association last
    assert [p["id"] for p in header["people"]] == [1, 61, 62]
    assert "tag_header" not in find(settings, include=("校园", "百合"))
    assert "tag_header" not in find(settings, include=("校园",), text="恋爱")


def test_facets_count_the_current_results(settings: Settings) -> None:
    facets = find(settings, include=("校园",))["facets"]
    assert {f["name"]: f["n"] for f in facets["tags"]} == {"百合": 2, "恋爱": 2, "恐怖": 1}
    assert facets["status"] == [{"name": "已完结", "n": 2}]


def test_read_filter_without_state_db(settings: Settings) -> None:
    assert find(settings, readable=True)["total"] == 0
    assert {i["readable"] for i in find(settings)["items"]} == {"unknown"}


def test_read_filter_with_state_db(settings: Settings) -> None:
    a, b = (s.id for s in settings.enabled_sources[:2])
    off = next(s.id for s in settings.sources if not s.enabled)
    write_state(
        settings,
        [
            (101, a, "yes", 1, "t", 1, ago(1)),
            (101, b, "no", None, None, None, ago(1)),
            (104, a, "maybe", 2, "t", 0, ago(0.5)),
            (102, a, "yes", 3, "t", 1, ago(40)),  # stale hit
            (103, a, "no", None, None, None, ago(1)),
            (103, off, "yes", 4, "t", 1, ago(1)),  # a disabled source does not count
            (108, a, "no", None, None, None, ago(10)),  # stale miss
        ],
    )
    assert sorted(ids(settings, readable=True)) == [101, 104]
    readable = {i["id"]: i["readable"] for i in find(settings)["items"]}
    assert readable[101] == "yes" and readable[104] == "maybe" and readable[103] == "no"
    assert readable[102] == "unknown" and readable[108] == "unknown"


def test_state_db_without_the_table_is_tolerated(settings: Settings) -> None:
    write_state(settings, None)
    assert find(settings, readable=True)["total"] == 0
    assert work(open_catalog(settings), settings, 101)["availability"] == []  # type: ignore[index]


HEAT = [101, 104, 103, 102, 108, 110, 111, 204, 112]  # every work by heat


def added(days: float) -> str:
    return state.to_text(state.utc_now() - timedelta(days=days))


def write_favorites(settings: Settings, rows: list[tuple[int, str]]) -> None:
    with state.connect(settings) as con:  # the schema the app creates
        con.executemany("INSERT INTO favorite VALUES (?, ?)", rows)


def favorite_flags(settings: Settings, **kw: Any) -> dict[int, bool]:
    return {i["id"]: i["favorite"] for i in find(settings, **kw)["items"]}


def test_favorites_without_state_db(settings: Settings) -> None:
    assert find(settings, favorite=True)["total"] == 0
    assert set(favorite_flags(settings).values()) == {False}
    assert ids(settings, sort="fav") == HEAT  # nothing to put first
    assert work(open_catalog(settings), settings, 101)["favorite"] is False  # type: ignore[index]


def test_favorites_filter_and_sort(settings: Settings) -> None:
    write_favorites(settings, [(103, added(3)), (112, added(1)), (102, added(2))])
    assert ids(settings, favorite=True, sort="fav") == [112, 102, 103]  # the latest first
    assert ids(settings, sort="fav") == [112, 102, 103, 101, 104, 108, 110, 111, 204]
    assert ids(settings, include=("校园",), sort="fav") == [102, 101, 104]
    # 恐怖 takes 104 and 103 from everything, so the works it drops are counted instead
    assert ids(settings, exclude=("恐怖",), sort="fav") == [112, 102, 101, 108, 110, 111, 204]
    assert sorted(ids(settings, favorite=True, include=("校园",))) == [102]
    flags = favorite_flags(settings)
    assert [w for w in HEAT if flags[w]] == [103, 102, 112]
    h = open_catalog(settings)
    assert work(h, settings, 112)["favorite"] is True  # type: ignore[index]
    assert work(h, settings, 101)["favorite"] is False  # type: ignore[index]


def test_favorites_that_have_become_readable(settings: Settings) -> None:
    source = settings.enabled_sources[0].id
    write_state(
        settings,
        [(101, source, "yes", 1, "t", 1, ago(1)), (103, source, "no", None, None, None, ago(1))],
    )
    write_favorites(settings, [(101, added(2)), (103, added(1)), (104, added(3))])
    assert ids(settings, favorite=True, readable=True) == [101]


def test_suggest(settings: Settings) -> None:
    h = open_catalog(settings)
    got = suggest(h, settings, "gl")
    assert got["tags"] == [{"name": "百合", "grp": "genre", "n": 2}]
    got = suggest(h, settings, "恋爱")
    assert [t["name"] for t in got["tags"]] == ["恋爱"] and got["works"][0]["id"] == 102
    got = suggest(h, settings, "#恐")
    assert [t["name"] for t in got["tags"]] == ["恐怖"] and got["works"] == got["people"] == []
    got = suggest(h, settings, "作者:浦沢")
    assert [p["id"] for p in got["people"]] == [60] and got["tags"] == got["works"] == []
    assert suggest(h, settings, "nakatani")["people"][0]["name"] == "仲谷鳰"  # romanized alias
    assert suggest(h, settings, " ") == {"tags": [], "works": [], "people": []}


def test_work_detail(settings: Settings) -> None:
    h = open_catalog(settings)
    detail = work(h, settings, 101)
    assert detail is not None
    assert detail["kind"] == "series" and detail["status"] == "已完结" and detail["vols"] == 8
    assert detail["region"] == "日本" and detail["region_inferred"] is False
    assert sorted(detail["aliases"]) == ["Bloom Into You", "成为你", "終將成為妳"]
    assert detail["summary"] == "两个女孩。"
    bangumi = {"official": True, "weak": False, "sources": ["bangumi"]}
    assert detail["tags"][:2] == [
        {"name": "百合", "grp": "genre", "votes": 35, **bangumi},
        {"name": "校园", "grp": "genre", "votes": 12, **bangumi},
    ]
    assert detail["pages"] == []  # no other source in this build
    assert [(p["name"], p["role"]) for p in detail["people"]] == [
        ("仲谷鳰", "作者"), ("集英社", "出版社"), ("コミック電撃大王", "连载杂志"),
    ]  # fmt: skip
    assert detail["relations"] == [
        {"relation": "改编", "id": 301, "title": "终将成为你", "year": None, "type": "动画",
         "in_catalog": False},
        {"relation": "续集", "id": 104, "title": "百合怪谈", "year": 2019, "type": "书籍",
         "in_catalog": True},
    ]  # fmt: skip
    assert detail["readable"] == "unknown"
    infer = work(h, settings, 111)
    assert infer is not None and infer["region_inferred"] is True
    standin = work(h, settings, 110)
    assert standin is not None
    assert [(p["name"], p["linked"]) for p in standin["people"]] == [
        ("浦沢直樹", True), ("新人作家", False),
    ]  # fmt: skip
    assert work(h, settings, 105) is None  # adult: not in the catalogue


def test_work_detail_lists_cached_availability(settings: Settings) -> None:
    write_state(
        settings,
        [
            (101, "zh-c", "no", None, None, None, ago(1)),
            (101, "zh-a", "yes", 7, "终将成为你", 1, ago(1)),
            (101, "zh-off", "yes", 8, "终将成为你", 1, ago(1)),  # disabled: hidden
        ],
    )
    detail = work(open_catalog(settings), settings, 101)
    assert detail is not None and detail["readable"] == "yes"
    assert [a["source"] for a in detail["availability"]] == [
        "zh-a", "zh-c",
    ]  # configured source order  # fmt: skip
    assert detail["availability"][0]["author_match"] is True
    assert detail["availability"][0]["fresh"] is True


@pytest.mark.parametrize("text", ["2010-2020", "2010-", "-2000", "2015"])
def test_parse_years(text: str) -> None:
    lo, hi = parse_years(text)
    assert lo is not None or hi is not None


def test_parse_years_rejects_junk() -> None:
    with pytest.raises(ValueError):
        parse_years("abc")
