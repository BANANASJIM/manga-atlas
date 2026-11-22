"""Chosen tags leave the facet list, so the response says which group each one belongs to, and
under the spelling the page asked for, which the catalogue may fold into another name."""

from pathlib import Path
from typing import Any

import pytest
from tests.atlas_support import Atlas

from manga_atlas.config import Settings
from manga_atlas.query.db import open_catalog
from manga_atlas.query.search import Query, search


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    atlas = Atlas(tmp_path)
    for wid in (1, 2, 3):
        atlas.work(wid)
    for tid, name, grp in [
        (1, "百合", "genre"),
        (2, "GL", "audience"),
        (3, "校园", "genre"),
        (4, "神作", "tag"),
    ]:
        atlas.tag(tid, name, grp)
    atlas.alias("yuri", 1)  # a synonym; 校園 and gl fold by themselves
    for wid, tid in [(1, 1), (1, 2), (1, 3), (2, 1), (2, 3), (3, 2), (3, 4)]:
        atlas.tagged(wid, tid, 5)
    atlas.save()
    return Settings(data_dir=tmp_path)


def find(settings: Settings, **kw: Any) -> dict[str, Any]:
    return search(open_catalog(settings), settings, Query(**kw))


def test_each_chosen_tag_comes_with_its_own_group(settings: Settings) -> None:
    facets = find(settings, include=("校园",), exclude=("GL",))["facets"]
    assert facets["included"] == [{"query": "校园", "name": "校园", "grp": "genre"}]
    # keeps its count
    assert facets["excluded"] == [{"query": "GL", "name": "GL", "grp": "audience", "n": 1}]

    facets = find(settings, include=("GL", "神作"))["facets"]
    assert facets["included"] == [  # in the order asked
        {"query": "GL", "name": "GL", "grp": "audience"},
        {"query": "神作", "name": "神作", "grp": "tag"},
    ]
    assert facets["excluded"] == []


def test_a_folded_spelling_is_answered_as_asked(settings: Settings) -> None:
    # An alias (yuri is 百合), a Traditional spelling (校園 is 校园), a case-folded one (gl is GL):
    # each tag's group and count come back under the spelling the page holds in its chips.
    facets = find(settings, include=("校園", "yuri"), exclude=("gl",))["facets"]
    assert facets["included"] == [
        {"query": "校園", "name": "校园", "grp": "genre"},
        {"query": "yuri", "name": "百合", "grp": "genre"},
    ]
    assert facets["excluded"] == [{"query": "gl", "name": "GL", "grp": "audience", "n": 1}]


@pytest.mark.parametrize(
    ("spelling", "name", "grp"),
    [
        ("yuri", "百合", "genre"),
        ("校園", "校园", "genre"),
        ("gl", "GL", "audience"),
        ("Gl", "GL", "audience"),
    ],
)
def test_an_excluded_spelling_keeps_its_count(
    settings: Settings, spelling: str, name: str, grp: str
) -> None:
    [plain] = find(settings, exclude=(name,))["facets"]["excluded"]
    [folded] = find(settings, exclude=(spelling,))["facets"]["excluded"]
    assert folded == {"query": spelling, "name": name, "grp": grp, "n": plain["n"]}
    assert plain["n"] > 0


def test_every_spelling_asked_is_answered(settings: Settings) -> None:
    facets = find(settings, include=("百合", "yuri", "YURI"))["facets"]
    assert [(t["query"], t["name"]) for t in facets["included"]] == [
        ("百合", "百合"),
        ("yuri", "百合"),
        ("YURI", "百合"),
    ]


def test_a_search_that_cannot_match_still_names_the_tags_it_knows(settings: Settings) -> None:
    find(settings, exclude=("GL",))  # leaves rows from the previous search on this connection
    result = find(settings, include=("百合", "没有这个标签"), exclude=("GL", "没有这个标签"))
    assert result["total"] == 0 and result["items"] == []
    assert result["facets"]["included"] == [{"query": "百合", "name": "百合", "grp": "genre"}]
    assert result["facets"]["excluded"] == [  # nothing to remove, whatever was found before
        {"query": "GL", "name": "GL", "grp": "audience", "n": 0}
    ]


def test_read_only_before_state_db_exists_still_names_the_tags(settings: Settings) -> None:
    result = find(settings, include=("校園",), exclude=("gl",), readable=True)
    assert result["total"] == 0
    assert [(t["query"], t["grp"]) for t in result["facets"]["included"]] == [("校園", "genre")]
    assert [(t["query"], t["grp"]) for t in result["facets"]["excluded"]] == [("gl", "audience")]


def test_an_unknown_status_still_names_the_tags(settings: Settings) -> None:
    result = find(settings, include=("百合",), statuses=("不存在",))
    assert result["total"] == 0
    assert result["facets"]["included"] == [{"query": "百合", "name": "百合", "grp": "genre"}]


def test_nothing_chosen_nothing_listed(settings: Settings) -> None:
    facets = find(settings)["facets"]
    assert facets["included"] == [] and facets["excluded"] == []
    assert {t["name"]: t["grp"] for t in facets["tags"]}["GL"] == "audience"


def test_an_unknown_tag_matches_nothing_and_lists_nothing(settings: Settings) -> None:
    result = find(settings, include=("没有这个标签",))
    assert result["total"] == 0
    assert result["facets"]["included"] == [] and result["facets"]["excluded"] == []
    assert find(settings, exclude=("没有这个标签",))["facets"]["excluded"] == []
