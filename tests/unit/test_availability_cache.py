from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from manga_atlas.availability import cache, state
from manga_atlas.config import Settings

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def row(state_: cache.State, age_days: float, source: str = "s1", work: int = 1) -> cache.Row:
    return cache.Row(work, source, state_, None, None, None, NOW - timedelta(days=age_days))


@pytest.mark.parametrize(
    ("verdict", "age", "fresh"),
    [("yes", 29, True), ("yes", 31, False), ("maybe", 29, True), ("no", 2, True), ("no", 4, False)],
)
def test_ttl_by_verdict(verdict: cache.State, age: float, fresh: bool) -> None:
    assert cache.is_fresh(row(verdict, age), Settings(), NOW) is fresh


def test_ttls_come_from_settings() -> None:
    short = replace(Settings(), cache_hit_ttl_days=1, cache_miss_ttl_days=1)
    assert not cache.is_fresh(row("yes", 2), short, NOW)


def test_readable_is_the_best_fresh_state() -> None:
    s = Settings()
    assert cache.readable([], s, NOW) == "unknown"
    assert cache.readable([row("no", 1), row("maybe", 1, "s2")], s, NOW) == "maybe"
    assert cache.readable([row("maybe", 1), row("yes", 1, "s2")], s, NOW) == "yes"
    assert cache.readable([row("yes", 40), row("no", 1, "s2")], s, NOW) == "no"  # stale yes
    assert cache.readable([row("no", 5)], s, NOW) == "unknown"


def test_round_trip_and_schema(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    stored = cache.Row(7, "s1", "maybe", 99, "乒乓", None, NOW)
    with state.connect(settings) as con:
        cache.put(con, stored)
        cache.put(con, replace(stored, source_id="s2", state="yes", author_match=True))
    with state.connect(settings) as con:
        assert cache.get(con, 7, "s1") == stored
        assert cache.rows(con, 7)["s2"].author_match is True
        assert cache.rows(con, 8) == {}
        columns = [r[1] for r in con.execute("PRAGMA table_info(availability)")]
        assert columns == [
            "work_id", "source_id", "state", "manga_id", "title", "author_match", "checked_at",
            "via",
        ]  # fmt: skip
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        raw = con.execute("SELECT checked_at FROM availability LIMIT 1").fetchone()[0]
    assert raw == "2026-11-20T12:00:00Z"


def test_connect_creates_both_tables(tmp_path: Path) -> None:
    with state.connect(Settings(data_dir=tmp_path)) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"availability", "library"} <= tables
