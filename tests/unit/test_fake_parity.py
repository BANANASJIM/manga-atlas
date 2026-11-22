"""The fake backend the browser tests run against orders and cuts a result row's credits and tags,
and names the chosen tags, as the real search does, and files a library as the real 整理书架 does:
the same data goes into both."""

import asyncio
import random
from collections import Counter
from itertools import chain
from pathlib import Path
from typing import Any

import pytest
from tests.atlas_support import Atlas
from tests.e2e.fake_backend import (
    CREATOR_LABELS,
    MIN_VOTES,
    OFFICIAL_VOTES,
    ROW_AUTHORS,
    ROW_TAGS,
    Catalog,
    Fake,
    Params,
    Work,
    source_pages,
)
from tests.suwayomi_support import FakeSuwayomi, make_atlas
from tests.suwayomi_support import Work as AtlasWork

from manga_atlas.availability.service import Service
from manga_atlas.build.schema import ROLE_LABELS, TagSource
from manga_atlas.config import Settings
from manga_atlas.library.organize import organize
from manga_atlas.library.sync import entries
from manga_atlas.query.db import open_catalog
from manga_atlas.query.search import Query, items, search
from manga_atlas.suwayomi import Category, LibraryManga
from manga_atlas.text import key

ROLE_OF = {label: role for role, label in ROLE_LABELS.items()}
TAG_NAMES = ["百合", "GL", "校园", "恋爱", "恐怖", "悬疑", "奇幻", "科幻", "搞笑", "美食", "日常"]
SOURCE_NAMES = {"jawiki": ["デスゲーム", "高等学校（舞台）"], "yurimiru": ["年龄差", "前后辈"]}
ALIASES = {"yuri": "百合", "gl": "GL", "校園": "校园"}  # a synonym, a lower-case, a Traditional


def fake_work(
    wid: int,
    credits: list[tuple[int, str]],
    official: list[str],
    user: dict[str, int],
    summary: str,
    sourced: dict[str, list[str]] | None = None,
) -> Work:
    return {
        "id": wid, "title": f"作品{wid}", "name": "", "aliases": [], "year": 2000,
        "status": "连载中", "region": "日本", "score": 7.0, "votes": 10, "rank": None,
        "heat": wid, "vols": 1, "people": credits, "official": official, "user": user,
        "summary": summary, "cover": False,
        "pages": {key: {"title": "", "url": "", "tags": t} for key, t in (sourced or {}).items()},
    }  # fmt: skip


def build(
    tmp_path: Path,
    people: dict[int, str],
    tags: dict[str, dict[str, Any]],
    aliases: dict[str, str],
    works: dict[int, Work],
) -> tuple[Settings, Fake]:
    """One world in a real-schema atlas.db and in the fake."""
    atlas = Atlas(tmp_path)
    for pid, name in people.items():
        atlas.person(pid, name)
    for name, tag in tags.items():
        atlas.tag(tag["id"], name, tag["grp"])
    for spelling, name in aliases.items():
        if key(spelling) != key(name):  # the catalogue folds the others by itself
            atlas.alias(spelling, tags[name]["id"])
    for wid, w in works.items():
        atlas.work(wid, w["summary"] or None)
        for pid, label in w["people"]:
            atlas.credit(wid, pid, ROLE_OF[label])
        given = {TagSource[k.upper()]: page["tags"] for k, page in source_pages(w).items()}
        for name in dict.fromkeys([*w["official"], *w["user"], *chain(*given.values())]):
            official = name in w["official"]
            src = TagSource.BANGUMI if official or name in w["user"] else TagSource(0)
            for source, names in given.items():
                src |= source if name in names else TagSource(0)
            votes = OFFICIAL_VOTES if official else w["user"].get(name, 0)
            atlas.tagged(wid, tags[name]["id"], votes, official=official, src=src)
    atlas.save()
    catalog = Catalog([], tags, people, works, "", len(works), aliases)
    return Settings(data_dir=tmp_path), Fake(catalog)


def world(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    people = {pid: f"人{pid}" for pid in [*range(1, 9), *range(-5, 0)]}  # linked, infobox-only
    names = TAG_NAMES + [n for own in SOURCE_NAMES.values() for n in own]
    ids = rng.sample(range(1, 200), len(names))  # tag ids that do not follow the names
    tags = {
        name: {"id": i, "grp": rng.choice(("genre", "audience", "tag")), "official": False}
        for name, i in zip(names, ids, strict=True)
    }
    tags.update({n: {**tags[n], "grp": key} for key, own in SOURCE_NAMES.items() for n in own})
    works: dict[int, Work] = {}
    for wid in range(1, 81):
        asked = (
            (rng.choice(list(people)), rng.choice(list(ROLE_OF))) for _ in range(rng.randint(0, 14))
        )
        credits = list(dict.fromkeys(asked))  # unordered, every person and role mixed
        picked = rng.sample(TAG_NAMES, rng.randint(0, 11))
        official = [n for n in picked if rng.random() < 0.3]
        user = {n: rng.choice((1, 2, 2, 3, 3, 7, 7, 7)) for n in picked if n not in official}
        text = "".join(
            rng.choices("零一二三四五六七八九", k=rng.choice((0, 5, 159, 160, 161, 400)))
        )
        # Each source's tags: some merge into Bangumi's or the other source's, some do not.
        sourced = {key: rng.sample(names, rng.randint(0, 4)) for key in SOURCE_NAMES}
        works[wid] = fake_work(wid, credits, official, user, text, sourced)
    return {"people": people, "tags": tags, "aliases": ALIASES, "works": works}


@pytest.mark.parametrize("seed", [20261120, 7, 99])
def test_result_rows_are_cut_and_ordered_like_the_servers(tmp_path: Path, seed: int) -> None:
    data = world(seed)
    settings, fake = build(tmp_path, **data)
    rows = items(open_catalog(settings), settings, list(data["works"]))
    for row in rows:
        mine = fake.result(data["works"][row["id"]])
        for field in ("authors", "tags", "summary"):
            assert mine[field] == row[field], (row["id"], field)

    # The data has what makes the order matter: cut credits and tags, tied votes, both kinds of id.
    works = data["works"].values()
    creators = [{p for p, label in w["people"] if label in CREATOR_LABELS} for w in works]
    assert any(len(c) > ROW_AUTHORS for c in creators)
    assert any(len(fake.tags_of(w)) > ROW_TAGS for w in works)
    ties = [
        [v for v in w["user"].values() if v >= MIN_VOTES] for w in works
    ]  # votes of the user tags that count
    assert any(len(v) != len(set(v)) for v in ties)
    assert any(any(p < 0 for p in c) and any(p > 0 for p in c) for c in creators)
    # Other sources' tags: of their own, merged into a counted user tag, lifting a too-weak one,
    # and given by both sources.
    given = [(w, n) for w in works for page in source_pages(w).values() for n in page["tags"]]
    own = {n for names in SOURCE_NAMES.values() for n in names}
    assert any(n in own for _, n in given)
    assert any(w["user"].get(n, 0) >= MIN_VOTES for w, n in given)
    assert any(0 < w["user"].get(n, 0) < MIN_VOTES for w, n in given)
    both = [[set(p["tags"]) for p in source_pages(w).values()] for w in works]
    assert any(len(t) == 2 and t[0] & t[1] for t in both)


def test_the_orders_for_one_handmade_work(tmp_path: Path) -> None:
    people = {3: "三", 5: "五", 9: "九", 12: "十二", 90: "社", -2: "无链二", -7: "无链七"}
    ids = {"a": 9, "b": 5, "x": 2, "y": 1, "z": 3, "w": 4}  # tag ids, against the names' order
    tags = {name: {"id": i, "grp": "tag", "official": False} for name, i in ids.items()}
    credits = [
        (-7, "作者"), (3, "作者"), (12, "作者"), (-2, "作者"),
        (5, "作画"), (3, "作画"), (9, "原作"), (90, "出版社"),
    ]  # fmt: skip
    work = fake_work(1, credits, ["a", "b"], {"x": 9, "y": 9, "z": 5, "w": 1}, "")
    settings, fake = build(tmp_path, people, tags, {}, {1: work})

    [real] = items(open_catalog(settings), settings, [1])
    for row in (real, fake.result(work)):
        # Creators only, by role; in a role the linked first, then by id; 3 once; at most three.
        assert [p["id"] for p in row["authors"]] == [3, 12, -2]
        # Official first by tag id; then by votes, the tied 9s by tag id; w has too few votes.
        assert [t["name"] for t in row["tags"]] == ["b", "a", "y", "x", "z"]


@pytest.mark.parametrize(
    ("include", "exclude"),
    [
        (("校園",), ("gl",)),
        (("yuri", "GL"), ("校园", "校園")),
        ((), ("Gl", "yuri")),
        (("百合", "没有这个标签"), ("GL",)),  # nothing can match
        (("没有这个标签",), ()),
    ],
)
def test_chosen_tags_are_named_like_the_servers(
    tmp_path: Path, include: tuple[str, ...], exclude: tuple[str, ...]
) -> None:
    settings, fake = build(tmp_path, **world(20261120))
    real = search(open_catalog(settings), settings, Query(include=include, exclude=exclude))
    mine = fake.search(Params(t=include, x=exclude))
    assert mine["total"] == real["total"]
    for part in ("included", "excluded"):
        assert mine["facets"][part] == real["facets"][part], part


def test_a_filtered_person_is_named_like_the_servers(tmp_path: Path) -> None:
    data = world(20261120)
    settings, fake = build(tmp_path, **data)
    h = open_catalog(settings)
    for pid in [*data["people"], 99]:  # 99 is nobody
        real = search(h, settings, Query(people=(pid,)))["facets"]["people"]
        assert fake.search(Params(a=pid))["facets"]["people"] == real, pid

    # Some people hold two roles equally often, so the tie rule is part of what is compared.
    held: dict[int, Counter[str]] = {pid: Counter() for pid in data["people"]}
    for w in data["works"].values():
        for pid, label in w["people"]:
            held[pid][label] += 1
    top = [c.most_common(2) for c in held.values() if len(c) > 1]
    assert any(first[1] == second[1] for first, second in top)


# 整理书架's world: works as (id, name, Chinese name, author, {tag: votes, 0 = official}); library
# entries as (id, title, author, categories, the work it maps to).
SHELF_WORKS = [
    (1, "やがて君になる", "终将成为你", "仲谷鳰", {"百合": 0, "校园": 0}),
    (2, "安達としまむら", "安达与岛村", "入間人間", {"百合": 0, "校园": 0, "恋爱": 5}),
    (3, "葬送のフリーレン", "葬送的芙莉莲", "山田鐘人", {"奇幻": 0, "百合": 1, "恐怖": 1}),
]
SHELF = [
    (101, "终将成为你", "仲谷鳰", (), 1),
    (102, "安达与岛村", "入間人間", (7,), 2),
    (103, "葬送的芙莉莲", "山田鐘人", (), 3),
    (104, "无人知晓的同人本", None, (), None),
]
SHELF_CATEGORIES = {0: "Default", 7: "校园"}
ORGANIZE_RUNS = [
    # new, existing with an entry in it already, too few votes, unknown, a repeat, another script
    (["百合", "#校园 ", "恐怖", "没有这个标签", "百合", "校園"], False),
    (["百合", "校园"], True),
    (["百合", "校园", "恋爱"], False),  # after applying
]


def test_organize_files_the_library_like_the_server(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    make_atlas(
        settings.atlas_db,
        [AtlasWork(w, n, cn, authors=(a,), tags=t) for w, n, cn, a, t in SHELF_WORKS],
    )
    suwayomi = FakeSuwayomi()
    suwayomi.items = [LibraryManga(m, title, a, None, "s1", c) for m, title, a, c, _ in SHELF]
    suwayomi.cats = [Category(cid, name) for cid, name in SHELF_CATEGORIES.items()]

    async def real_runs() -> list[dict[str, Any]]:
        service = Service(settings, suwayomi)
        return [await organize(service, tags, apply=apply) for tags, apply in ORGANIZE_RUNS]

    real = asyncio.run(real_runs())
    assert {e.manga_id: e.work_id for e in entries(settings)} == {m[0]: m[-1] for m in SHELF}

    names = sorted({name for *_, given in SHELF_WORKS for name in given})
    works: dict[int, Work] = {
        w: {
            "id": w,
            "official": [n for n in t if not t[n]],
            "user": {n: v for n, v in t.items() if v},
        }
        for w, *_, t in SHELF_WORKS
    }
    library = {
        "categories": [{"id": cid, "name": name} for cid, name in SHELF_CATEGORIES.items()],
        "entries": [
            {"manga_id": m, "title": title, "work_id": w, "categories": list(c)}
            for m, title, _, c, w in SHELF
        ],
    }
    known = {n: {"id": i, "grp": "tag", "official": False} for i, n in enumerate(names, 1)}
    fake = Fake(Catalog([], known, {}, works, "", len(works), {"校園": "校园"}, library))
    fake.reset()
    assert [fake.organize(tags, apply) for tags, apply in ORGANIZE_RUNS] == real
