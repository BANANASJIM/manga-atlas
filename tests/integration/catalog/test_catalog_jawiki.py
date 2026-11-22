"""Japanese Wikipedia tags built into atlas.db from a cache, and how searches and the work page
read them."""

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from manga_atlas.build import pipeline
from manga_atlas.build.schema import TagSource
from manga_atlas.build.tagsources import common, jawiki
from manga_atlas.config import Settings
from manga_atlas.query.db import open_catalog
from manga_atlas.query.search import Query, search
from manga_atlas.query.suggest import suggest
from manga_atlas.query.work import work

MakeSettings = Callable[[Path], Settings]
Built = tuple[Settings, dict[str, Any]]  # settings, build summary
CACHE = {
    "works": {
        w: {"title": t, "article": a, "linked": False}
        for w, t, a in [
            ("101", "やがて君になる", "やがて君になる"),
            ("111", "やがて君になる 公式", "やがて君になる"),  # a redirect: 101 keeps the article
            ("103", "屍鬼", "屍鬼"),
            ("104", "百合と怪談", "百合と怪談"),
            ("112", "無名", "無名"),
        ]
    },
    "categories": {
        "やがて君になる": [
            "2015年の漫画", "恋愛漫画", "百合漫画", "生徒会を題材とした漫画作品",
            "高等学校を舞台とした漫画作品", "電撃コミックスの漫画作品", "漫画作品 や",
        ],
        "屍鬼": ["ホラー漫画", "村を舞台とした漫画作品", "漫画作品 し"],
        "百合と怪談": ["百合漫画", "高等学校を舞台とした漫画作品", "漫画作品 ゆ"],
        "無名": ["漫画作品 む", "曖昧さ回避"],  # a disambiguation page
    },
}  # fmt: skip


def rows(db: Path, sql: str) -> list[Any]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


@pytest.fixture
def built(tmp_path: Path, dump: Path, make_settings: MakeSettings) -> Built:
    settings = make_settings(tmp_path)
    common.write(settings.tag_cache("jawiki") / jawiki.CACHE, CACHE)
    return settings, pipeline.build(settings, dump)


def test_wikipedia_tags_merge_into_bangumi_tags_or_start_their_own(built: Built) -> None:
    settings, summary = built
    assert summary["jawiki_works"] == 3 and summary["jawiki_tags"] == 1
    marks = rows(
        settings.atlas_db,
        "SELECT wt.work_id, t.name, t.grp, wt.votes, wt.official, wt.src FROM work_tag wt"
        " JOIN tag t ON t.id = wt.tag_id WHERE wt.src & 2 ORDER BY wt.work_id, t.name",
    )
    both = TagSource.BANGUMI | TagSource.JAWIKI
    assert marks == [
        (101, "恋爱", "genre", 8, 0, both),  # 恋愛 folds into Bangumi's own tag
        (101, "百合", "genre", 35, 1, both),
        (101, "高等学校（舞台）", "jawiki", 0, 0, TagSource.JAWIKI),
        (103, "恐怖", "genre", 10, 0, both),  # ホラー, by the aliases
        (104, "百合", "genre", 5, 0, both),
        (104, "高等学校（舞台）", "jawiki", 0, 0, TagSource.JAWIKI),
    ]  # 生徒会 and 村（舞台）: one work each, too few for a tag of their own
    pages = rows(settings.atlas_db, "SELECT work_id, src, title, url FROM work_page")
    assert [(w, s, t) for w, s, t, _ in pages] == [
        (101, 2, "やがて君になる"), (103, 2, "屍鬼"), (104, 2, "百合と怪談"),
    ]  # fmt: skip
    assert pages[0][3] == jawiki.article_url(settings, "やがて君になる")
    labels = rows(settings.atlas_db, "SELECT code, name FROM label WHERE kind = 'tag_source'")
    assert labels == [(1, "Bangumi"), (2, "日文维基"), (4, "ゆりみる")]


def test_wikipedia_tags_count_without_votes(built: Built) -> None:
    settings, _ = built
    h = open_catalog(settings)
    found = search(h, settings, Query(include=("高等学校（舞台）",)))
    assert sorted(i["id"] for i in found["items"]) == [101, 104]
    assert found["tag_header"]["tag"] == {"name": "高等学校（舞台）", "grp": "jawiki", "n": 2}
    assert search(h, settings, Query(include=("ホラー",)))["total"] == 2  # 103, and 104 by votes
    everything = {t["name"]: t for t in search(h, settings, Query(sort="heat"))["facets"]["tags"]}
    assert everything["高等学校（舞台）"] == {"name": "高等学校（舞台）", "grp": "jawiki", "n": 2}
    [tag] = suggest(h, settings, "#高等")["tags"]
    assert tag == {"name": "高等学校（舞台）", "grp": "jawiki", "n": 2}
    row = search(h, settings, Query(include=("百合",), sort="heat"))["items"][0]
    assert row["id"] == 101 and row["tags"][-1] == {"name": "高等学校（舞台）", "grp": "jawiki"}


def test_the_work_page_names_each_tags_sources_and_the_article(built: Built) -> None:
    settings, _ = built
    detail = work(open_catalog(settings), settings, 101)
    assert detail is not None
    sources = {t["name"]: (t["sources"], t["weak"]) for t in detail["tags"]}
    assert sources["百合"] == (["bangumi", "jawiki"], False)
    assert sources["高等学校（舞台）"] == (["jawiki"], False)
    assert sources["校园"] == (["bangumi"], False)
    assert detail["pages"] == [
        {
            "source": "jawiki",
            "title": "やがて君になる",
            "url": jawiki.article_url(settings, "やがて君になる"),
        }
    ]


def test_an_offline_build_keeps_using_an_old_cache(
    tmp_path: Path, dump: Path, make_settings: MakeSettings
) -> None:
    settings = make_settings(tmp_path)
    path = settings.tag_cache("jawiki") / jawiki.CACHE
    common.write(path, CACHE)
    old = json.loads(path.read_text())
    old["fetched_at"] = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    path.write_text(json.dumps(old))
    summary = pipeline.build(settings, dump)  # the refresh fails: the tests are offline
    assert summary["jawiki_works"] == 3
    assert json.loads(path.read_text()) == old


def test_a_disabled_source_gives_nothing(
    tmp_path: Path, dump: Path, make_settings: MakeSettings
) -> None:
    on = make_settings(tmp_path)
    settings = Settings(**{**on.__dict__, "jawiki_enabled": False})
    common.write(settings.tag_cache("jawiki") / jawiki.CACHE, CACHE)
    summary = pipeline.build(settings, dump)
    assert "jawiki_works" not in summary
    assert rows(settings.atlas_db, "SELECT count(*) FROM work_tag WHERE src & 2") == [(0,)]
