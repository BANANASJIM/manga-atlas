import asyncio
import itertools
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace

import pytest

from manga_atlas.availability.executor import BreakerOpen, Executor, SourceGate
from manga_atlas.config import Settings, Source
from manga_atlas.suwayomi import MangaGone, SourceFailure, SourceTimeout, SuwayomiUnavailable

INTERVAL = 0.05


def gate(clock: list[float] | None = None, **changes: float) -> SourceGate:
    """A gate with no spacing, a second to answer and no hold after a timeout, but `changes`."""
    base = replace(
        Settings(), source_interval_s=0.0, source_timeout_s=1.0, suwayomi_source_timeout_s=0.0
    )
    return SourceGate(
        "s", replace(base, **changes), (lambda: clock[0]) if clock else time.monotonic
    )


async def request[T](g: SourceGate, op: Callable[[], Awaitable[T]]) -> T:
    """One request in a session of its own."""
    async with g.session() as s:
        return await s.request(op)


async def ok() -> str:
    return "ok"


async def fail() -> str:
    raise SourceFailure("boom")


def test_spacing_within_a_source_and_parallel_across_sources() -> None:
    settings = replace(
        Settings(),
        sources=(Source("a", "A"), Source("b", "B")),
        source_interval_s=INTERVAL,
        source_timeout_s=1.0,
    )
    executor = Executor(settings)
    log: list[tuple[str, float, float]] = []
    in_flight: dict[str, int] = {"a": 0, "b": 0}
    peak: dict[str, int] = {"a": 0, "b": 0}

    async def work(source: str) -> None:
        in_flight[source] += 1
        peak[source] = max(peak[source], in_flight[source])
        start = time.monotonic()
        await asyncio.sleep(0.02)
        in_flight[source] -= 1
        log.append((source, start, time.monotonic()))

    async def main() -> float:
        t0 = time.monotonic()
        await asyncio.gather(*(request(executor.gate(s), lambda s=s: work(s)) for s in "aaabbb"))
        return time.monotonic() - t0

    elapsed = asyncio.run(main())
    assert peak == {"a": 1, "b": 1}  # never two in flight on one source
    for source in "ab":
        calls = [c for c in log if c[0] == source]
        gaps = [nxt[1] - prev[2] for prev, nxt in itertools.pairwise(calls)]
        assert all(g >= INTERVAL * 0.95 for g in gaps), gaps
    # a and b overlap: three spaced calls take ~3*0.02 + 2*0.05; serial would be twice that
    assert elapsed < 2 * (3 * 0.02 + 2 * INTERVAL)


def test_timeout_counts_as_failure_and_holds_the_source() -> None:
    g = gate(source_timeout_s=0.05, suwayomi_source_timeout_s=30.0)

    async def hang() -> str:
        await asyncio.sleep(1)
        return "late"

    with pytest.raises(SourceTimeout):
        asyncio.run(request(g, hang))
    assert g.failures == 1 and g.retry_in() == 0
    # Suwayomi may still be fetching: the next request waits out its own timeout
    assert g._next_start - time.monotonic() > 29


def test_breaker_opens_fails_fast_and_recovers() -> None:
    clock = [100.0]
    g = gate(clock, breaker_failures=2, breaker_cooldown_s=600.0)
    calls = 0

    async def counted_fail() -> str:
        nonlocal calls
        calls += 1
        raise SourceFailure("boom")

    for _ in range(2):
        with pytest.raises(SourceFailure):
            asyncio.run(request(g, counted_fail))
    assert g.retry_in() > 0 and g.describe() == {"breaker": "open", "retry_in_s": 600.0}
    with pytest.raises(BreakerOpen):
        asyncio.run(request(g, counted_fail))
    assert calls == 2  # fail fast: no third request reached the source
    clock[0] += 601
    with pytest.raises(SourceFailure):  # half-open: one try, fails, opens again
        asyncio.run(request(g, counted_fail))
    assert g.retry_in() > 0 and calls == 3
    clock[0] += 601
    assert asyncio.run(request(g, ok)) == "ok"
    assert g.retry_in() == 0 and g.failures == 0


def test_success_resets_the_failure_count() -> None:
    g = gate(breaker_failures=2)
    with pytest.raises(SourceFailure):
        asyncio.run(request(g, fail))
    asyncio.run(request(g, ok))
    with pytest.raises(SourceFailure):
        asyncio.run(request(g, fail))
    assert g.retry_in() == 0


def test_suwayomi_down_and_dead_entries_do_not_trip_the_breaker() -> None:
    g = gate(breaker_failures=1)

    async def down() -> str:
        raise SuwayomiUnavailable("refused")

    async def gone() -> str:
        raise MangaGone("漫畫未找到")

    for op in (down, down, gone):
        with pytest.raises((SuwayomiUnavailable, MangaGone)):
            asyncio.run(request(g, op))
    assert g.retry_in() == 0


def test_session_holds_the_source_between_requests() -> None:
    g = gate(source_interval_s=INTERVAL)
    order: list[str] = []

    async def step(name: str) -> str:
        order.append(name)
        return name

    async def first() -> None:
        async with g.session() as s:
            await s.request(lambda: step("search-1"))
            await s.request(lambda: step("details-1"))

    async def second() -> None:
        await asyncio.sleep(0)  # queue behind the first session
        await request(g, lambda: step("search-2"))

    async def main() -> None:
        await asyncio.gather(first(), second())

    asyncio.run(main())
    assert order == ["search-1", "details-1", "search-2"]


def test_a_cancelled_request_holds_the_source_until_the_call_ends() -> None:
    g = gate()
    serving: list[str] = []  # what the site is answering right now
    peak = 0

    async def site(name: str) -> str:  # Suwayomi talking to the site: our cancel can't stop it
        nonlocal peak
        serving.append(name)
        peak = max(peak, len(serving))
        await asyncio.sleep(0.1)
        serving.remove(name)
        return name

    def call(name: str) -> Awaitable[str]:
        return asyncio.shield(asyncio.ensure_future(site(name)))

    async def main() -> None:
        first = asyncio.create_task(request(g, lambda: call("first")))
        await asyncio.sleep(0.02)
        first.cancel()  # the user left the work page
        await asyncio.sleep(0.02)
        first.cancel()  # ...and the server is shutting the stream down too
        await asyncio.sleep(0.02)
        assert await request(g, lambda: call("second")) == "second"
        with pytest.raises(asyncio.CancelledError):
            await first

    asyncio.run(main())
    assert peak == 1
