"""Against the real Suwayomi: `uv run pytest -m live tests/live -s`.

About ten requests in all, paced like the app (one at a time per source, spacing from settings).
Never adds to the library and never writes categories.
"""

import asyncio
import time
from dataclasses import replace
from pathlib import Path

import pytest

from manga_atlas.availability.checker import Checker, SourceResult
from manga_atlas.availability.executor import Executor
from manga_atlas.config import Source, load
from manga_atlas.match import Matcher, WorkNames
from manga_atlas.suwayomi import client_for

pytestmark = pytest.mark.live

DRIFTING_CLASSROOM = WorkNames(1, "漂流教室", "漂流教室", authors=("楳図かずお", "楳图一雄"))
SPY_FAMILY = WorkNames(2, "SPY×FAMILY", "间谍过家家", authors=("遠藤達哉", "远藤达哉"))
SHONEN_JUMP_PLUS = "5161357527629843269"


async def timed(checker: Checker, names: WorkNames, source: Source) -> tuple[SourceResult, float]:
    start = time.monotonic()
    result = await checker.check_source(
        names, checker.matcher.work_keys(names), source, details=True, refresh=True
    )
    return result, time.monotonic() - start


def test_known_works_are_found(tmp_path: Path) -> None:
    settings = replace(load(), data_dir=tmp_path)  # your config.toml's sources
    zh = [s for s in settings.enabled_sources if s.lang.startswith("zh")]
    jump = next((s for s in settings.enabled_sources if s.id == SHONEN_JUMP_PLUS), None)
    if not zh and jump is None:
        pytest.skip("no Chinese source and no Shonen Jump+ configured")

    async def main() -> list[tuple[SourceResult, float]]:
        client = client_for(settings)
        try:
            assert await client.ping()
            for source in settings.enabled_sources:
                assert await client.source_exists(source.id), source.name
            checker = Checker(settings, client, Executor(settings), Matcher(settings))
            jobs = [timed(checker, DRIFTING_CLASSROOM, s) for s in zh]
            if jump is not None:
                jobs.append(timed(checker, SPY_FAMILY, jump))
            return await asyncio.gather(*jobs)
        finally:
            await client.aclose()

    results = asyncio.run(main())
    for r, seconds in results:
        note = r.error or f"{r.title!r} author_match={r.author_match}"
        print(f"{r.source.name}: {r.state} {note} {seconds:.2f}s")
    found = {r.source.id: r for r, _ in results}
    if zh:
        assert any(found[s.id].state in ("yes", "maybe") for s in zh)
    if jump is not None:
        assert found[SHONEN_JUMP_PLUS].state == "yes", found[SHONEN_JUMP_PLUS]
