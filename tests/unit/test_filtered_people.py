"""The people a search is limited to come back with the role they hold most, for the chip."""

from pathlib import Path
from typing import Any

import pytest
from tests.atlas_support import Atlas

from manga_atlas.build.schema import CREATOR_ROLES, ROLE_LABELS, Role
from manga_atlas.config import Settings
from manga_atlas.query.db import open_catalog
from manga_atlas.query.search import Query, search


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    atlas = Atlas(tmp_path)
    for wid in range(1, 6):
        atlas.work(wid)
    for pid, name in [
        (1, "兼任者"),
        (2, "平局者"),
        (3, "出版社甲"),
        (4, "杂志甲"),
        (5, "没有作品的人"),
        (-9, "只在简介里的人"),
    ]:
        atlas.person(pid, name)
    for person, work, role in [
        (1, 1, Role.ORIGINAL),
        (1, 2, Role.ORIGINAL),
        (1, 3, Role.AUTHOR),  # two works as 原作 beat one as 作者
        (2, 1, Role.ARTIST),
        (2, 2, Role.AUTHOR),  # one each: the lower role code
        (3, 1, Role.PUBLISHER),
        (3, 2, Role.PUBLISHER),
        (3, 3, Role.PUBLISHER),
        (3, 4, Role.ORIGINAL),
        (4, 1, Role.MAGAZINE),
        (-9, 5, Role.AUTHOR),  # a name found only in an infobox
    ]:
        atlas.credit(work, person, role)
    atlas.save()
    return Settings(data_dir=tmp_path)


def named(settings: Settings, **kw: Any) -> list[dict[str, Any]]:
    people: list[dict[str, Any]] = search(open_catalog(settings), settings, Query(**kw))["facets"][
        "people"
    ]
    return people


def test_each_person_comes_with_the_role_they_hold_most(settings: Settings) -> None:
    assert named(settings, people=(1, 2, 3, 4, -9)) == [
        {"id": 1, "name": "兼任者", "role": "原作", "creator": True},
        {"id": 2, "name": "平局者", "role": "作者", "creator": True},
        {"id": 3, "name": "出版社甲", "role": "出版社", "creator": False},
        {"id": 4, "name": "杂志甲", "role": "连载杂志", "creator": False},
        {"id": -9, "name": "只在简介里的人", "role": "作者", "creator": True},
    ]


def test_they_come_in_the_order_asked_each_once_and_unknown_ones_are_left_out(
    settings: Settings,
) -> None:
    assert [p["id"] for p in named(settings, people=(4, 5, 999, 1, 4))] == [4, 1]
    assert named(settings) == []


def test_a_search_that_cannot_match_still_names_its_people(settings: Settings) -> None:
    result = search(open_catalog(settings), settings, Query(people=(3,), include=("没有这个标签",)))
    assert result["total"] == 0
    assert [(p["id"], p["role"]) for p in result["facets"]["people"]] == [(3, "出版社")]


def test_the_creator_flag_follows_the_creator_roles(tmp_path: Path) -> None:
    atlas = Atlas(tmp_path)
    atlas.work(1)
    for role in Role:  # one person per role, each credited once
        atlas.person(int(role), f"人{role.name}")
        atlas.credit(1, int(role), role)
    atlas.save()
    settings = Settings(data_dir=tmp_path)

    people = named(settings, people=tuple(int(r) for r in Role))
    assert {p["role"]: p["creator"] for p in people} == {
        ROLE_LABELS[r]: r in CREATOR_ROLES for r in Role
    }
    assert {p["role"]: p["creator"] for p in people}["出版社"] is False
