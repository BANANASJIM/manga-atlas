"""Reading an entry first gives it the chapters Suwayomi's web UI would not fetch itself."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

import pytest
from tests.suwayomi_support import FakeSuwayomi

from manga_atlas.availability.chapters import ensure_chapters
from manga_atlas.availability.service import Service
from manga_atlas.config import Settings, Source
from manga_atlas.suwayomi import (
    GraphQLError,
    MangaGone,
    MangaState,
    SourceFailure,
    SourceTimeout,
    SuwayomiUnavailable,
)

EMPTY = MangaState("z1", True, 0)  # initialized by an availability check, never given chapters


def settings_for(tmp_path: Path) -> Settings:
    return replace(
        Settings(data_dir=tmp_path),
        sources=(Source("z1", "中一"), Source("z2", "中二")),
        source_interval_s=0.0,
        source_timeout_s=1.0,
    )


def run(
    settings: Settings, fake: FakeSuwayomi, scenario: Callable[[Service], Awaitable[object]]
) -> None:
    async def main() -> None:
        await scenario(Service(settings, fake))

    asyncio.run(main())


def opening(*manga_ids: int) -> Callable[[Service], Awaitable[object]]:
    """Open these entries all at once."""

    async def scenario(service: Service) -> object:
        return await asyncio.gather(*(ensure_chapters(service, m) for m in manga_ids))

    return scenario


def entry(
    fake: FakeSuwayomi, manga_id: int, state: MangaState | Exception, fetch: int | Exception
) -> None:
    fake.states[manga_id] = state
    fake.fetches[manga_id] = fetch


def test_an_initialized_entry_without_chapters_gets_them_once(tmp_path: Path) -> None:
    fake = FakeSuwayomi()
    entry(fake, 7, EMPTY, 17)
    run(settings_for(tmp_path), fake, opening(7))
    assert [c.arg for c in fake.of("fetch_chapters")] == ["7"]
    assert fake.states[7] == MangaState("z1", True, 17)


@pytest.mark.parametrize(
    "state", [MangaState("z1", False, 0), MangaState("z1", True, 17)], ids=["uninitialized", "full"]
)
def test_an_entry_the_web_ui_can_open_costs_no_source_request(
    tmp_path: Path, state: MangaState
) -> None:
    fake = FakeSuwayomi()
    entry(fake, 7, state, 99)
    run(settings_for(tmp_path), fake, opening(7))
    # One read of Suwayomi's own database: the entry never queued for its source.
    assert [c.kind for c in fake.calls] == ["manga_state"]


def test_two_tabs_opening_one_entry_fetch_it_once(tmp_path: Path) -> None:
    fake = FakeSuwayomi(delay=0.01)
    entry(fake, 7, EMPTY, 17)
    run(settings_for(tmp_path), fake, opening(7, 7))
    assert len(fake.of("fetch_chapters")) == 1  # the second found them when its turn came


def test_the_chapter_fetch_goes_through_that_sources_gate(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    fake = FakeSuwayomi()
    entry(fake, 7, EMPTY, SourceFailure("boom"))

    async def three_times(service: Service) -> None:
        for _ in range(settings.breaker_failures + 1):
            await ensure_chapters(service, 7)
        assert service.executor.gate("z1").describe()["breaker"] == "open"  # its failures count
        assert service.executor.gate("z2").describe()["breaker"] == "closed"

    run(settings, fake, three_times)
    assert len(fake.of("fetch_chapters")) == settings.breaker_failures  # then the breaker held


@pytest.mark.parametrize(
    "failure",
    [SuwayomiUnavailable("down"), GraphQLError("no such manga")],
    ids=lambda failure: type(failure).__name__,
)
def test_an_unreadable_state_means_no_fetch_and_no_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, failure: Exception
) -> None:
    fake = FakeSuwayomi()
    entry(fake, 7, failure, 17)
    run(settings_for(tmp_path), fake, opening(7))
    assert fake.of("fetch_chapters") == []
    assert "manga 7" in caplog.text


@pytest.mark.parametrize(
    "failure",
    [
        SourceFailure("boom"),
        SourceTimeout("timed out"),
        MangaGone("漫畫未找到"),
        SuwayomiUnavailable("down"),
    ],
    ids=lambda failure: type(failure).__name__,
)
def test_a_failed_fetch_is_logged_and_never_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, failure: Exception
) -> None:
    fake = FakeSuwayomi()
    entry(fake, 7, EMPTY, failure)
    run(settings_for(tmp_path), fake, opening(7))
    assert len(fake.of("fetch_chapters")) == 1
    [record] = caplog.records
    assert "manga 7" in record.getMessage() and record.levelno == logging.WARNING
    assert record.exc_info is None  # a failure the app expects needs no traceback


def test_an_odd_reply_is_logged_with_its_traceback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeSuwayomi()
    entry(fake, 7, EMPTY, KeyError("chapters"))
    run(settings_for(tmp_path), fake, opening(7))
    [record] = caplog.records
    assert "manga 7" in record.getMessage() and record.levelno == logging.ERROR
    assert record.exc_info is not None


def test_a_source_this_app_does_not_pace_is_not_asked(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeSuwayomi()
    entry(fake, 7, MangaState("elsewhere", True, 0), 17)
    run(settings_for(tmp_path), fake, opening(7))
    assert fake.of("fetch_chapters") == []
    assert caplog.records == []  # not an error: nothing to log
