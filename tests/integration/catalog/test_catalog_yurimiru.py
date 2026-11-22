"""ゆりみる tags built into atlas.db from a cache: matched by Japanese title at build time,
translated, merged into Bangumi's tags or grouped as its own, and read by search and the work
page."""

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from manga_atlas.build import pipeline
from manga_atlas.build.schema import TagSource
from manga_atlas.build.tagsources import common, yurimiru
from manga_atlas.config import Settings
from manga_atlas.query.db import open_catalog
from manga_atlas.query.search import Query, search
from manga_atlas.query.work import work

MakeSettings = Callable[[Path], Settings]
URL = "https://www.yurimiru.com/works/{}/vol/1"


def entry(n: int, title: str, medium: str = "漫画", **facts: Any) -> tuple[str, dict[str, Any]]:
    site_id = f"00000000-0000-4000-8000-{n:012d}"
    base = {"tags": [], "romance": False, "scales": {}}
    return site_id, {
        "url": URL.format(site_id),
        "lastmod": "2026-11-01T00:00:00Z",
        "title": title,
        "medium": medium,
        **base,
        **facts,
    }


ENTRIES = dict(
    [
        entry(1, "やがて君になる", tags=["学園", "先輩後輩", "連載中"], romance=True),
        entry(2, "百合と怪談", tags=["ホラー", "先輩後輩"], scales={"糖度": 20}),
        entry(3, "屍鬼", medium="小説", tags=["ホラー"]),  # the novel: never the manga
    ]
)


def rows(db: Path, sql: str) -> list[Any]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


@pytest.fixture
def built(tmp_path: Path, dump: Path, make_settings: MakeSettings) -> tuple[Settings, Any]:
    settings = make_settings(tmp_path)
    common.write(
        settings.tag_cache("yurimiru") / yurimiru.CACHE,
        {"entries": ENTRIES},
    )
    return settings, pipeline.build(settings, dump)


def test_entries_are_matched_and_their_tags_merged_or_grouped(
    built: tuple[Settings, dict[str, Any]],
) -> None:
    settings, summary = built
    assert summary["yurimiru_works"] == 2 and summary["yurimiru_tags"] == 1
    marks = rows(
        settings.atlas_db,
        "SELECT wt.work_id, t.name, t.grp, wt.src FROM work_tag wt JOIN tag t ON t.id = wt.tag_id"
        f" WHERE wt.src & {int(TagSource.YURIMIRU)} ORDER BY wt.work_id, t.name",
    )
    both = TagSource.BANGUMI | TagSource.YURIMIRU
    assert marks == [
        (101, "前后辈", "yurimiru", TagSource.YURIMIRU),
        (101, "校园", "genre", both),  # 学園
        (101, "百合", "genre", both),  # marked 恋愛百合
        (104, "前后辈", "yurimiru", TagSource.YURIMIRU),
        (104, "恐怖", "genre", both),  # ホラー; not sweet enough for 百合 from here
    ]
    pages = rows(settings.atlas_db, "SELECT work_id, src, title, url FROM work_page")
    assert pages == [
        (101, 4, "やがて君になる", ENTRIES["00000000-0000-4000-8000-000000000001"]["url"]),
        (104, 4, "百合と怪談", ENTRIES["00000000-0000-4000-8000-000000000002"]["url"]),
    ]


def test_search_and_the_work_page_read_them(built: tuple[Settings, dict[str, Any]]) -> None:
    settings, _ = built
    h = open_catalog(settings)
    found = search(h, settings, Query(include=("前后辈",)))
    assert sorted(i["id"] for i in found["items"]) == [101, 104]
    detail = work(h, settings, 101)
    assert detail is not None
    sources = {t["name"]: t["sources"] for t in detail["tags"]}
    assert sources["前后辈"] == ["yurimiru"] and sources["百合"] == ["bangumi", "yurimiru"]
    assert [p["source"] for p in detail["pages"]] == ["yurimiru"]
