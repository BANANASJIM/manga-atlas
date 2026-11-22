import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from tests.suwayomi_support import FakeSuwayomi, Work, make_atlas, manga

from manga_atlas.availability import cache, state
from manga_atlas.availability.checker import Checker, SourceResult
from manga_atlas.availability.executor import Executor
from manga_atlas.config import Settings, Source
from manga_atlas.match import Matcher
from manga_atlas.suwayomi import MangaGone, SourceFailure

Z1, Z2, J1 = Source("z1", "中一"), Source("z2", "中二"), Source("j1", "日一", lang="ja")
TITAN = Work(1, "進撃の巨人", "进击的巨人", authors=("諫山創",), collects=900)
PINGPONG = Work(2, "ピンポン", "乒乓", authors=("松本大洋",), collects=800)


def setup(
    tmp_path: Path, sources: tuple[Source, ...] = (Z1, Z2), works: tuple[Work, ...] = ()
) -> Settings:
    settings = replace(
        Settings(data_dir=tmp_path),
        sources=sources,
        source_interval_s=0.0,
        source_timeout_s=1.0,
    )
    make_atlas(settings.atlas_db, works or (TITAN, PINGPONG))
    return settings


def run(
    settings: Settings,
    fake: FakeSuwayomi,
    scenario: Callable[[Checker], Awaitable[object]],
) -> object:
    async def main() -> object:
        checker = Checker(settings, fake, Executor(settings), Matcher(settings))
        return await scenario(checker)

    return asyncio.run(main())


async def work_page(
    checker: Checker, work_id: int, refresh: bool = False
) -> dict[str, SourceResult]:
    names, keys = checker.prepare(work_id)
    return {r.source.id: r async for r in checker.check_work(names, keys, refresh=refresh)}


def cached(settings: Settings, work_id: int) -> dict[str, cache.Row]:
    with state.connect(settings) as con:
        return cache.rows(con, work_id)


def test_long_title_is_yes_and_details_confirm_the_author(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1,))
    fake = FakeSuwayomi()
    fake.searches[("z1", "进击的巨人")] = [manga(10, "進擊的巨人"), manga(11, "進擊的巨人 外傳")]
    fake.details[10] = manga(10, "進擊的巨人", author="諫山創")
    results = run(settings, fake, lambda c: work_page(c, 1))
    assert isinstance(results, dict)
    r = results["z1"]
    assert (r.state, r.manga_id, r.author_match, r.cached) == ("yes", 10, True, False)
    assert [c.arg for c in fake.of("manga")] == ["10"]
    assert cached(settings, 1)["z1"].state == "yes"


def test_short_title_needs_an_author_for_yes(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1,))
    fake = FakeSuwayomi()
    fake.searches[("z1", "乒乓")] = [manga(20, "乒乓"), manga(21, "乒乓")]
    fake.details[20] = manga(20, "乒乓", author="Melord花爷")  # same title, another work
    fake.details[21] = manga(21, "乒乓", author="松本大洋")

    async def scenario(checker: Checker) -> tuple[SourceResult, dict[str, SourceResult]]:
        names, keys = checker.prepare(2)
        batch = await checker.check_source(names, keys, Z1, details=False)
        page = await work_page(checker, 2)  # the unconfirmed ≈ is asked again
        return batch, page

    out = run(settings, fake, scenario)
    assert isinstance(out, tuple)
    batch, page = out
    assert (batch.state, batch.author_match) == ("maybe", None)
    assert (page["z1"].state, page["z1"].manga_id, page["z1"].author_match) == ("yes", 21, True)
    assert len(fake.of("search")) == 2 and len(fake.of("manga")) == 2


def test_dead_entries_are_not_readable(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1,))
    fake = FakeSuwayomi()
    fake.searches[("z1", "进击的巨人")] = [manga(10, "進擊的巨人")]
    fake.details[10] = MangaGone("漫畫未找到!")
    results = run(settings, fake, lambda c: work_page(c, 1))
    assert isinstance(results, dict) and results["z1"].state == "no"


def test_failures_are_reported_down_and_never_cached(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1, Z2))
    fake = FakeSuwayomi()
    fake.searches[("z1", "进击的巨人")] = SourceFailure("Connect timed out")
    fake.searches[("z2", "进击的巨人")] = SourceFailure("Connect timed out")

    async def scenario(checker: Checker) -> list[dict[str, SourceResult]]:
        first = await work_page(checker, 1)
        second = await work_page(checker, 1)  # z1/z2 breakers open after 2 failures
        third = await work_page(checker, 1)
        return [first, second, third]

    out = run(settings, fake, scenario)
    assert isinstance(out, list)
    assert {r.state for page in out for r in page.values()} == {"down"}
    assert out[0]["z1"].error == "Connect timed out"
    assert out[2]["z1"].error is not None and out[2]["z1"].error.startswith("paused")
    assert len(fake.of("search")) == 4  # the third round never reached the sources
    assert cached(settings, 1) == {}


def test_suwayomi_down_is_down_for_every_source(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1, Z2))
    fake = FakeSuwayomi()
    fake.down = True
    results = run(settings, fake, lambda c: work_page(c, 1))
    assert isinstance(results, dict)
    assert {r.error for r in results.values()} == {"Suwayomi unreachable"}
    assert cached(settings, 1) == {}


def test_cache_is_reused_until_refresh(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1,))
    fake = FakeSuwayomi()
    fake.searches[("z1", "进击的巨人")] = [manga(10, "進擊的巨人", author="諫山創")]

    async def scenario(checker: Checker) -> list[SourceResult]:
        a = await work_page(checker, 1)
        b = await work_page(checker, 1)
        c = await work_page(checker, 1, refresh=True)
        return [a["z1"], b["z1"], c["z1"]]

    out = run(settings, fake, scenario)
    assert isinstance(out, list)
    assert [r.cached for r in out] == [False, True, False]
    assert len(fake.of("search")) == 2


def test_expired_cache_is_checked_again(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1,))
    old = state.utc_now() - timedelta(days=settings.cache_miss_ttl_days + 1)
    with state.connect(settings) as con:
        cache.put(con, cache.Row(1, "z1", "no", None, None, None, old))
    fake = FakeSuwayomi()
    fake.searches[("z1", "进击的巨人")] = [manga(10, "進擊的巨人", author="諫山創")]
    results = run(settings, fake, lambda c: work_page(c, 1))
    assert isinstance(results, dict) and results["z1"].state == "yes"


def test_only_the_first_results_are_compared(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1,))
    fake = FakeSuwayomi()
    others = [manga(30 + i, f"别的漫画{i}") for i in range(settings.match_top_n)]
    fake.searches[("z1", "进击的巨人")] = [*others, manga(10, "進擊的巨人")]
    results = run(settings, fake, lambda c: work_page(c, 1))
    assert isinstance(results, dict) and results["z1"].state == "no"


def test_japanese_sources_search_the_original_title(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1, J1))
    fake = FakeSuwayomi()
    fake.searches[("j1", "進撃の巨人")] = [manga(40, "進撃の巨人", source="j1", author="諫山創")]
    results = run(settings, fake, lambda c: work_page(c, 1))
    assert isinstance(results, dict)
    assert sorted(c.arg for c in fake.of("search")) == ["j1:進撃の巨人", "z1:进击的巨人"]
    assert results["j1"].state == "yes" and results["z1"].state == "no"


def test_check_many_goes_chinese_first_and_stops_at_the_first_hit(tmp_path: Path) -> None:
    settings = setup(tmp_path, (J1, Z1, Z2))  # configured with the Japanese source first
    fake = FakeSuwayomi()
    fake.searches[("z1", "进击的巨人")] = [manga(10, "進擊的巨人")]
    fake.searches[("z2", "乒乓")] = [manga(21, "乒乓")]

    async def scenario(checker: Checker) -> list[dict[str, object]]:
        ids = [1, 2, 999]
        return [line async for line in checker.check_many(ids, checker.load(ids))]

    lines = run(settings, fake, scenario)
    assert isinstance(lines, list)
    by_id = {line["id"]: line["readable"] for line in lines}
    assert by_id == {1: "yes", 2: "maybe", 999: "unknown"}
    assert sorted(line["done"] for line in lines) == [1, 2, 3]
    assert {line["total"] for line in lines} == {3}
    searched = [c.arg for c in fake.of("search")]
    assert "z2:进击的巨人" not in searched  # stopped at z1's hit
    assert not any(a.startswith("j1:") for a in searched)  # Japanese only after both missed
    assert not fake.of("manga")  # no details calls in a page check
    assert len(run(settings, fake, scenario)) == 3  # cached now: no new searches
    assert len(fake.of("search")) == len(searched)


def test_page_check_ignores_a_disabled_sources_cached_hit(tmp_path: Path) -> None:
    off = Source("off", "关", enabled=False)
    settings = setup(tmp_path, (Z1, off))
    with state.connect(settings) as con:
        cache.put(con, cache.Row(1, "off", "yes", 9, "进击的巨人", True, state.utc_now()))
    fake = FakeSuwayomi()

    async def scenario(checker: Checker) -> list[dict[str, object]]:
        return [line async for line in checker.check_many([1], checker.load([1]))]

    assert run(settings, fake, scenario) == [{"id": 1, "readable": "no", "done": 1, "total": 1}]
    assert [c.arg for c in fake.of("search")] == ["z1:进击的巨人"]


def test_page_check_runs_a_bounded_number_of_works(tmp_path: Path) -> None:
    settings = replace(setup(tmp_path, (Z1, Z2)), check_parallel_works=1)
    fake = FakeSuwayomi()
    fake.searches[("z2", "进击的巨人")] = [manga(10, "進擊的巨人")]
    fake.searches[("z2", "乒乓")] = [manga(21, "乒乓")]

    async def scenario(checker: Checker) -> object:
        return [line async for line in checker.check_many([1, 2], checker.load([1, 2]))]

    run(settings, fake, scenario)
    order = [c.arg for c in fake.of("search")]
    assert order == ["z1:进击的巨人", "z2:进击的巨人", "z1:乒乓", "z2:乒乓"]


def test_an_odd_reply_is_down_for_that_source_only(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1, Z2))
    fake = FakeSuwayomi()
    fake.searches[("z1", "进击的巨人")] = KeyError("mangas")
    fake.searches[("z2", "进击的巨人")] = [manga(10, "進擊的巨人", author="諫山創")]
    results = run(settings, fake, lambda c: work_page(c, 1))
    assert isinstance(results, dict)
    assert (results["z1"].state, results["z1"].error) == ("down", "unexpected reply")
    assert results["z2"].state == "yes"


def test_page_check_looks_past_a_same_title_with_another_author(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1, Z2))
    fake = FakeSuwayomi()
    fake.searches[("z1", "乒乓")] = [manga(20, "乒乓", author="Melord花爷")]
    fake.searches[("z2", "乒乓")] = [manga(21, "乒乓", author="松本大洋")]

    async def scenario(checker: Checker) -> object:
        return [line async for line in checker.check_many([2], checker.load([2]))]

    lines = run(settings, fake, scenario)
    assert isinstance(lines, list) and lines[0]["readable"] == "yes"
    assert [c.arg for c in fake.of("search")] == ["z1:乒乓", "z2:乒乓"]
    other = cached(settings, 2)["z1"]
    assert (other.state, other.manga_id, other.title, other.author_match) == (
        "no",
        20,
        "乒乓",
        False,
    )
    run(settings, fake, scenario)  # settled by the cached ✓: no new searches
    assert len(fake.of("search")) == 2


def test_short_title_by_another_author_is_not_readable(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1,))
    fake = FakeSuwayomi()
    fake.searches[("z1", "乒乓")] = [manga(20, "乒乓")]
    fake.details[20] = manga(20, "乒乓", author="Melord花爷")
    results = run(settings, fake, lambda c: work_page(c, 2))
    assert isinstance(results, dict)
    line = results["z1"].to_json()
    assert (line["state"], line["manga_id"], line["title"]) == ("no", 20, "乒乓")
    assert line["author_match"] is False  # the UI can say 同名不同作
    rows = cached(settings, 2)
    assert cache.readable(rows.values(), settings, state.utc_now()) == "no"


def test_long_title_by_another_author_stays_yes(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1,))
    fake = FakeSuwayomi()
    fake.searches[("z1", "进击的巨人")] = [manga(10, "進擊的巨人", author="东立出版社")]
    results = run(settings, fake, lambda c: work_page(c, 1))
    assert isinstance(results, dict)
    assert (results["z1"].state, results["z1"].author_match) == ("yes", False)


def test_unknown_author_beats_a_known_other_one(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1,))
    fake = FakeSuwayomi()
    fake.searches[("z1", "乒乓")] = [manga(20, "乒乓", author="Melord花爷"), manga(21, "乒乓")]

    async def scenario(checker: Checker) -> SourceResult:
        names, keys = checker.prepare(2)
        return await checker.check_source(names, keys, Z1, details=False)

    result = run(settings, fake, scenario)
    assert isinstance(result, SourceResult)
    assert (result.state, result.manga_id, result.author_match) == ("maybe", 21, None)


# ---- works Bangumi gives no Chinese title

GUILTY = Work(3, "ぎるてぃらいぶらり", "", aliases=("Guilty Library",), authors=("閉伊川広輔",))
Z3 = Source("z3", "中三")
# Sources whose listings name authors: only their titles are searched on the others.
K1, K2 = Source("z1", "中一", lists_authors=True), Source("z2", "中二", lists_authors=True)


def test_another_translation_is_maybe_and_other_chinese_sources_search_it(tmp_path: Path) -> None:
    settings = setup(tmp_path, (K1, Z2, J1), (GUILTY,))
    fake = FakeSuwayomi()
    fake.searches[("z1", "ぎるてぃらいぶらり")] = [
        manga(20, "罪惡圖書室", author="閉伊川広輔"),
        manga(21, "家庭教師太XX", author="多喜れい"),
    ]
    fake.searches[("z2", "罪恶图书室")] = [manga(30, "罪恶图书室")]  # found by the learned title
    fake.details[30] = manga(30, "罪恶图书室", author="閉伊川広輔")
    fake.searches[("j1", "ぎるてぃらいぶらり")] = [
        manga(40, "別作品", source="j1", author="閉伊川広輔")
    ]

    out = run(settings, fake, lambda c: work_page(c, 3))
    assert isinstance(out, dict)
    z1, z2 = out["z1"], out["z2"]
    assert (z1.state, z1.title, z1.author_match, z1.renamed) == ("maybe", "罪惡圖書室", True, True)
    assert (z2.state, z2.query, z2.author_match) == ("maybe", "罪恶图书室", True)
    assert out["j1"].state == "no"  # a Japanese source never vouches
    # z2 searched the Japanese and English titles, then the learned one alone.
    assert [c.arg for c in fake.of("search") if c.arg.startswith("z2")] == [
        "z2:ぎるてぃらいぶらり", "z2:Guilty Library", "z2:罪恶图书室",
    ]  # fmt: skip
    checker = Checker(settings, fake, Executor(settings), Matcher(settings))
    # Only z1 vouched for the title; z2 found it by that title, so it vouches for nothing.
    assert checker.load([3])[3].learned == (("罪惡圖書室", "z1"),)
    assert cached(settings, 3)["z2"].via == "罪恶图书室"


def test_no_vouching_when_the_authors_other_works_are_listed_or_lower(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1, Z2, Z3), (GUILTY,))
    fake = FakeSuwayomi()
    fake.searches[("z1", "ぎるてぃらいぶらり")] = [
        manga(20, "罪惡圖書室", author="閉伊川広輔"),
        manga(21, "罪惡圖書室 外傳", author="閉伊川広輔"),  # a sibling listed too: ambiguous
    ]
    fake.searches[("z2", "ぎるてぃらいぶらり")] = [
        manga(22, "家庭教師太XX", author="多喜れい"),
        manga(23, "罪惡圖書室", author="閉伊川広輔"),  # not the top result
    ]
    fake.searches[("z3", "ぎるてぃらいぶらり")] = [manga(24, "罪惡圖書室", author="別人")]
    out = run(settings, fake, lambda c: work_page(c, 3))
    assert isinstance(out, dict)
    assert {k: r.state for k, r in out.items()} == {"z1": "no", "z2": "no", "z3": "no"}


def test_a_work_with_a_chinese_title_is_never_vouched_for(tmp_path: Path) -> None:
    settings = setup(tmp_path, (Z1,))
    fake = FakeSuwayomi()
    fake.searches[("z1", "进击的巨人")] = [manga(10, "巨人戰記", author="諫山創")]
    out = run(settings, fake, lambda c: work_page(c, 1))
    assert isinstance(out, dict) and out["z1"].state == "no"
    assert [c.arg for c in fake.of("search")] == ["z1:进击的巨人"]  # and searched once


def test_a_miss_older_than_a_learned_title_is_asked_again(tmp_path: Path) -> None:
    # 检查本页 first: z1 (searched first) misses, z2 vouches from its listing and stops the round.
    settings = setup(tmp_path, (Z1, K2), (GUILTY,))
    fake = FakeSuwayomi()
    fake.searches[("z2", "ぎるてぃらいぶらり")] = [manga(20, "罪惡圖書室", author="閉伊川広輔")]

    async def page_check(checker: Checker) -> object:
        return [line async for line in checker.check_many([3], checker.load([3]))]

    with state.connect(settings) as con:  # z1's miss, in the same second as z2's find below
        cache.put(con, cache.Row(3, "z1", "no", None, None, None, state.utc_now()))
    lines = run(settings, fake, page_check)
    assert isinstance(lines, list) and lines[0]["readable"] == "maybe"
    fake.searches[("z1", "罪恶图书室")] = [manga(30, "罪恶图书室", author="閉伊川広輔")]
    out = run(settings, fake, lambda c: work_page(c, 3))
    assert isinstance(out, dict) and out["z1"].state == "maybe"  # not the cached miss
    assert "z1:罪恶图书室" in [c.arg for c in fake.of("search")]


def test_a_wrong_vouch_ends_on_recheck_though_its_title_still_finds_it(tmp_path: Path) -> None:
    settings = setup(tmp_path, (K1, Z2), (GUILTY,))
    fake = FakeSuwayomi()
    other = manga(20, "别的书", author="閉伊川広輔")  # the author's other work, listed first
    fake.searches[("z1", "ぎるてぃらいぶらり")] = [other]
    fake.searches[("z1", "别的书")] = [other]
    fake.searches[("z2", "别的书")] = [manga(30, "别的书", author="閉伊川広輔")]
    out = run(settings, fake, lambda c: work_page(c, 3))
    assert isinstance(out, dict) and (out["z1"].state, out["z2"].state) == ("maybe", "maybe")

    # 重新检查 asks by the work's own names; z1 now lists the right work too, so it cannot tell.
    fake.searches[("z1", "ぎるてぃらいぶらり")] = [
        other,
        manga(21, "罪惡圖書室", author="閉伊川広輔"),
    ]
    out = run(settings, fake, lambda c: work_page(c, 3, refresh=True))
    assert isinstance(out, dict) and (out["z1"].state, out["z2"].state) == ("no", "no")
    checker = Checker(settings, fake, Executor(settings), Matcher(settings))
    assert checker.load([3])[3].learned == ()


def test_a_row_found_by_a_title_goes_stale_with_its_voucher(tmp_path: Path) -> None:
    settings = setup(tmp_path, (K1, Z2), (GUILTY,))
    fake = FakeSuwayomi()
    fake.searches[("z1", "ぎるてぃらいぶらり")] = [manga(20, "罪惡圖書室", author="閉伊川広輔")]
    fake.searches[("z2", "罪恶图书室")] = [manga(30, "罪恶图书室", author="閉伊川広輔")]
    run(settings, fake, lambda c: work_page(c, 3))
    with state.connect(settings) as con:  # z1 no longer vouches (checked again elsewhere)
        cache.put(con, cache.Row(3, "z1", "no", None, None, None, state.utc_now()))
    searches = len(fake.of("search"))
    out = run(settings, fake, lambda c: work_page(c, 3))
    assert isinstance(out, dict) and out["z2"].state == "no" and not out["z2"].cached
    assert len(fake.of("search")) > searches  # z2 asked again, by the work's own names


def test_a_source_that_cannot_see_listing_authors_spreads_nothing(tmp_path: Path) -> None:
    # z2 lists no authors, so it cannot see the author's other work listed above the right one:
    # its ≈ stays its own, and z1 (which saw both and said no) is not asked with its title.
    settings = setup(tmp_path, (K1, Z2), (GUILTY,))
    fake = FakeSuwayomi()
    fake.searches[("z1", "ぎるてぃらいぶらり")] = [
        manga(20, "别的书", author="閉伊川広輔"),
        manga(21, "罪惡圖書室", author="閉伊川広輔"),
    ]
    fake.searches[("z2", "ぎるてぃらいぶらり")] = [manga(30, "别的书"), manga(31, "罪惡圖書室")]
    fake.details[30] = manga(30, "别的书", author="閉伊川広輔")
    out = run(settings, fake, lambda c: work_page(c, 3))
    assert isinstance(out, dict) and (out["z1"].state, out["z2"].state) == ("no", "maybe")
    assert "z1:别的书" not in [c.arg for c in fake.of("search")]


def test_a_hit_by_the_works_own_names_is_never_marked_as_found_by_another(tmp_path: Path) -> None:
    # The Simplified spelling of a Traditional alias (並 -> 并) need not share its key, and a
    # page check searches a Latin Chinese title the work page leaves out.
    alias = Work(5, "気になってる人が男じゃなかった", "在意的人不是他",
                 aliases=("我在意的對象並不是男人",), authors=("作者甲",))  # fmt: skip
    latin = Work(6, "ラストゲーム", "Last Game", authors=("天乃忍",))
    settings = setup(tmp_path, (Z1,), (alias, latin))
    fake = FakeSuwayomi()
    fake.searches[("z1", "我在意的对象并不是男人")] = [
        manga(10, "我在意的对象并不是男人", author="作者甲")
    ]
    fake.searches[("z1", "Last Game")] = [manga(11, "Last Game", author="天乃忍")]

    async def scenario(checker: Checker) -> None:
        await work_page(checker, 5)
        [line async for line in checker.check_many([6], checker.load([6]))]

    run(settings, fake, scenario)
    assert cached(settings, 5)["z1"].state == "yes" and cached(settings, 5)["z1"].via is None
    assert cached(settings, 6)["z1"].state == "yes" and cached(settings, 6)["z1"].via is None
    searched = len(fake.of("search"))
    run(settings, fake, lambda c: work_page(c, 5))
    assert len(fake.of("search")) == searched  # served from the cache
