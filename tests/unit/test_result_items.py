"""Result rows: creators, tags and summary for a page of works, read in batches."""

from dataclasses import replace
from pathlib import Path

import pytest
from tests.atlas_support import Atlas

from manga_atlas.build.schema import Role
from manga_atlas.config import Settings
from manga_atlas.query.db import Handle, open_catalog
from manga_atlas.query.search import Query, items, search

SUMMARY = "零一二三四五六七八九" * 20  # 200 characters, 600 bytes


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    atlas = Atlas(tmp_path)
    atlas.work(1, SUMMARY)
    atlas.work(2, "短简介")
    atlas.work(3)  # no summary row, no credits, no tags
    for pid, name in [
        (10, "甲"),
        (11, "乙"),
        (12, "丙"),
        (13, "丁"),
        (20, "出版社甲"),
        (21, "杂志甲"),
    ]:
        atlas.person(pid, name)
    atlas.person(-5, "只在简介里的人")  # a credit found only in the infobox
    for person, role in [
        (-5, Role.AUTHOR),
        (10, Role.AUTHOR),
        (11, Role.ARTIST),
        (10, Role.ARTIST),  # a second role for the same person
        (12, Role.ORIGINAL),
        (13, Role.SCRIPT),
        (20, Role.PUBLISHER),
        (21, Role.MAGAZINE),
    ]:
        atlas.credit(1, person, role)
    atlas.credit(2, 20, Role.PUBLISHER)  # nothing but a publisher
    for tid, name, grp in [
        (1, "官方5票", "genre"),
        (2, "官方9票", "audience"),
        (3, "官方0票", "tag"),
        (4, "用户50票", "tag"),
        (5, "用户9票", "tag"),
        (6, "用户3票", "tag"),
        (7, "用户2票", "tag"),
        (8, "用户1票", "tag"),
    ]:
        atlas.tag(tid, name, grp)
    for tid, votes, official in [
        (1, 5, True),
        (2, 9, True),
        (3, 0, True),
        (4, 50, False),
        (5, 9, False),
        (6, 3, False),
        (7, 2, False),
        (8, 1, False),  # too few votes for the facets to count it
    ]:
        atlas.tagged(1, tid, votes, official)
    atlas.save()
    return Settings(data_dir=tmp_path)


def page(settings: Settings, ids: list[int]) -> list[dict[str, object]]:
    return items(open_catalog(settings), settings, ids)


def test_every_row_has_the_three_fields(settings: Settings) -> None:
    rows = page(settings, [3, 1, 2])
    assert [r["id"] for r in rows] == [3, 1, 2]
    for row in rows:
        assert {"authors", "tags", "summary"} <= row.keys()
    bare = rows[0]
    assert bare["authors"] == [] and bare["tags"] == [] and bare["summary"] == ""


def test_search_rows_carry_them_too(settings: Settings) -> None:
    result = search(open_catalog(settings), settings, Query(include=("官方9票",)))
    assert [r["id"] for r in result["items"]] == [1]
    assert result["items"][0]["authors"] and result["items"][0]["summary"]


def test_authors_are_creators_in_work_page_order(settings: Settings) -> None:
    wide = replace(settings, item_authors_max=10)
    first = page(wide, [1])[0]["authors"]
    # By role; linked before infobox-only within a role; a person once; no publisher or magazine.
    assert [(p["id"], p["name"]) for p in first] == [
        (10, "甲"), (-5, "只在简介里的人"), (11, "乙"), (12, "丙"), (13, "丁"),
    ]  # fmt: skip
    assert page(wide, [2])[0]["authors"] == []  # a publisher is not an author


def test_authors_stop_at_the_configured_count(settings: Settings) -> None:
    assert [p["id"] for p in page(settings, [1])[0]["authors"]] == [10, -5, 11]
    narrow = replace(settings, item_authors_max=1)
    assert [p["id"] for p in page(narrow, [1])[0]["authors"]] == [10]


def test_tags_are_what_facets_count_official_first_then_by_votes(settings: Settings) -> None:
    wide = replace(settings, item_tags_max=10)
    names = [t["name"] for t in page(wide, [1])[0]["tags"]]
    assert names == ["官方9票", "官方5票", "官方0票", "用户50票", "用户9票", "用户3票", "用户2票"]
    assert page(wide, [1])[0]["tags"][0] == {"name": "官方9票", "grp": "audience"}
    strict = replace(wide, tag_min_votes=4)
    names = [t["name"] for t in page(strict, [1])[0]["tags"]]
    assert names == ["官方9票", "官方5票", "官方0票", "用户50票", "用户9票"]


def test_tags_stop_at_the_configured_count(settings: Settings) -> None:
    assert len(page(settings, [1])[0]["tags"]) == settings.item_tags_max == 6
    narrow = replace(settings, item_tags_max=2)
    assert [t["name"] for t in page(narrow, [1])[0]["tags"]] == ["官方9票", "官方5票"]


def test_summary_is_cut_by_characters(settings: Settings) -> None:
    long, short, none = (r["summary"] for r in page(settings, [1, 2, 3]))
    assert long == SUMMARY[: settings.item_summary_chars] and len(str(long)) == 160
    assert short == "短简介" and none == ""
    assert page(replace(settings, item_summary_chars=5), [1])[0]["summary"] == "零一二三四"


def test_no_works_no_work(settings: Settings) -> None:
    assert page(settings, []) == []


def test_a_page_costs_the_same_statements_whatever_its_size(settings: Settings) -> None:
    h: Handle = open_catalog(settings)

    def statements(ids: list[int]) -> int:
        seen: list[str] = []
        h.con.set_trace_callback(seen.append)
        items(h, settings, ids)
        h.con.set_trace_callback(None)
        return len(seen)

    assert statements([1]) == statements([1, 2, 3])
