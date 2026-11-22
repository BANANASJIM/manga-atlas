"""Chinese titles beyond Bangumi's at build time: Wikidata labels become aliases (matching and
search use them); titles Chinese sources list a work under, vouched for by its author, become
searchable but never aliases."""

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from manga_atlas.availability import cache, state
from manga_atlas.build import pipeline, zhnames
from manga_atlas.build.tagsources import common
from manga_atlas.config import Settings, Source
from manga_atlas.query.db import open_catalog
from manga_atlas.query.search import Query, search

MakeSettings = Callable[[Path], Settings]


def aliases(db: Path, work_id: int) -> list[str]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT alias FROM work_alias WHERE work_id = ?", (work_id,))
        return [a for (a,) in rows]
    finally:
        con.close()


def found(settings: Settings, text: str) -> list[int]:
    return [i["id"] for i in search(open_catalog(settings), settings, Query(text=text))["items"]]


def test_wikidata_labels_are_aliases_and_source_titles_only_searchable(
    tmp_path: Path, dump: Path, make_settings: MakeSettings
) -> None:
    settings = replace(make_settings(tmp_path), sources=(Source("z1", "中一"),))
    labels = {"101": ["終將成為妳", "終將成為妳 外傳"]}  # a side story's label stays out
    common.write(settings.tag_cache(zhnames.KEY) / zhnames.CACHE, {"labels": labels})
    now = state.utc_now()
    with state.connect(settings) as con:
        cache.put(con, cache.Row(103, "z1", "maybe", 7, "屍鬼新譯", True, now))  # author vouched
        cache.put(con, cache.Row(104, "z1", "maybe", 8, "百合怪谈新译", None, now))  # no author

    pipeline.build(settings, dump)
    assert "終將成為妳" in aliases(settings.atlas_db, 101)
    assert "終將成為妳 外傳" not in aliases(settings.atlas_db, 101)
    assert found(settings, "终将成为妳") == [101]
    assert "屍鬼新譯" not in aliases(settings.atlas_db, 103)
    assert found(settings, "尸鬼新译") == [103]
    assert found(settings, "百合怪谈新译") == []
