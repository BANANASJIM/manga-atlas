import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from tests.suwayomi_support import FakeSuwayomi, Work, make_atlas

from manga_atlas.availability import state
from manga_atlas.availability.service import Service
from manga_atlas.config import Settings
from manga_atlas.library.organize import organize
from manga_atlas.library.sync import entries, sync
from manga_atlas.suwayomi import Category, LibraryManga

WORKS = [
    Work(1, "進撃の巨人", "进击的巨人", authors=("諫山創",), collects=900),
    Work(2, "ピンポン", "乒乓", authors=("松本大洋",), collects=800, tags={"百合": 3, "恐怖": 1}),
    Work(
        3, "魔男のイチ", "魔男伊奇", authors=("宇佐崎しろ", "西修"), collects=300, tags={"百合": 5}
    ),
    Work(5, "乒乓", "乒乓", authors=("Melord花爷",), collects=5000),  # same title, more popular
]


def lib(
    mid: int, title: str, author: str | None = None, cats: tuple[int, ...] = ()
) -> LibraryManga:
    return LibraryManga(mid, title, author, None, "s1", cats)


def library_fake() -> FakeSuwayomi:
    fake = FakeSuwayomi()
    fake.items = [
        lib(101, "魔男伊奇", "宇佐崎 しろ, 西修", cats=(1,)),
        lib(102, "乒乓", "松本大洋"),
        lib(103, "乒乓"),
        lib(104, "未知漫画"),
    ]
    fake.cats = [Category(0, "Default"), Category(1, "恐怖")]
    return fake


def run[T](settings: Settings, fake: FakeSuwayomi, body: Callable[[Service], Awaitable[T]]) -> T:
    async def main() -> T:
        return await body(Service(settings, fake))

    return asyncio.run(main())


def mapping(settings: Settings) -> dict[int, int | None]:
    return {e.manga_id: e.work_id for e in entries(settings)}


def test_sync_maps_titles_with_author_then_popularity(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    make_atlas(settings.atlas_db, WORKS)
    fake = library_fake()
    run(settings, fake, lambda svc: sync(svc))
    assert mapping(settings) == {101: 3, 102: 2, 103: 5, 104: None}


def test_a_short_title_by_another_author_stays_unmapped(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    make_atlas(settings.atlas_db, WORKS[:2])  # 进击的巨人 by 諫山創, 乒乓 by 松本大洋
    fake = FakeSuwayomi()
    fake.items = [
        lib(201, "乒乓", "Melord花爷"),  # 同名不同作
        lib(202, "乒乓"),  # author unknown: the title is all there is
        lib(203, "进击的巨人", "someone else"),  # long titles: authors confirm, never veto
    ]
    run(settings, fake, lambda svc: sync(svc))
    assert mapping(settings) == {201: None, 202: 2, 203: 1}


def test_sync_ttl_and_force(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    fake = library_fake()

    async def body(svc: Service) -> list[object]:
        return [await sync(svc), await sync(svc), await sync(svc, force=True)]

    first, second, third = run(settings, fake, body)
    assert first is not None and second is None and third is not None
    assert len(fake.of("library")) == 2
    assert mapping(settings) == {101: None, 102: None, 103: None, 104: None}  # no catalogue yet


def test_sync_remaps_new_renamed_and_after_a_rebuild(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    make_atlas(settings.atlas_db, WORKS)
    fake = library_fake()
    run(settings, fake, lambda svc: sync(svc))

    # Same titles, catalogue not rebuilt since: mappings are kept even if the data changed.
    settings.atlas_db.unlink()
    make_atlas(settings.atlas_db, [w for w in WORKS if w.id != 3])
    past = time.time() - 3600
    os.utime(settings.atlas_db, (past, past))
    fake.items = [*fake.items[:3], lib(104, "进击的巨人")]  # renamed entry
    run(settings, fake, lambda svc: sync(svc))
    assert mapping(settings) == {101: 3, 102: 2, 103: 5, 104: 1}

    # A rebuilt catalogue remaps everything; entries gone from the library are dropped.
    future = time.time() + 3600
    os.utime(settings.atlas_db, (future, future))
    fake.items = fake.items[:3]
    run(settings, fake, lambda svc: sync(svc))
    assert mapping(settings) == {101: None, 102: 2, 103: 5}


def test_organize_preview_writes_nothing(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    make_atlas(settings.atlas_db, WORKS)
    fake = library_fake()
    result: dict[str, Any] = run(
        settings, fake, lambda svc: organize(svc, ["百合", "#恐怖", "不存在", "百合 "], apply=False)
    )
    groups = {g["name"]: g for g in result["categories"]}
    assert list(groups) == ["百合", "恐怖", "不存在"]
    assert (groups["百合"]["exists"], groups["百合"]["category_id"]) == (False, None)
    assert [m["manga_id"] for m in groups["百合"]["mangas"]] == [101, 102]
    assert groups["恐怖"]["exists"] and groups["恐怖"]["mangas"] == []  # 1 vote < tag_min_votes
    assert groups["不存在"]["known"] is False
    assert result["unmapped"] == [{"manga_id": 104, "title": "未知漫画"}]
    assert (result["created"], result["added"]) == ([], 0)
    assert not fake.of("create_category") and not fake.assigned


def test_organize_apply_creates_and_only_adds(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    make_atlas(settings.atlas_db, WORKS)
    fake = library_fake()

    async def body(svc: Service) -> list[dict[str, Any]]:
        return [
            await organize(svc, ["百合"], apply=True),
            await organize(svc, ["百合"], apply=True),
        ]

    first, second = run(settings, fake, body)
    assert first["created"] == ["百合"] and first["added"] == 2
    [group] = first["categories"]
    assert all(m["assigned"] for m in group["mangas"])
    new_id = group["category_id"]
    assert fake.assigned == [(new_id, [101, 102])]
    by_id = {m.id: m.category_ids for m in fake.items}
    assert by_id[101] == (1, new_id)  # kept its existing category
    assert by_id[103] == () and by_id[104] == ()
    assert (second["created"], second["added"]) == ([], 0)
    assert {c.arg for c in fake.of("create_category")} == {"百合"}


def test_state_rows_use_the_plan_schema(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    run(settings, library_fake(), lambda svc: sync(svc))
    with state.connect(settings) as con:
        columns = [r[1] for r in con.execute("PRAGMA table_info(library)")]
    assert columns == ["manga_id", "source_id", "title", "work_id", "synced_at"]
