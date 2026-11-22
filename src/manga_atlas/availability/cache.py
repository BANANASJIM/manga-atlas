"""The availability table: one row per work x source. Failures are never stored."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from manga_atlas.availability.state import from_text, to_text
from manga_atlas.config import Settings

State = Literal["yes", "maybe", "no"]
Readable = Literal["yes", "maybe", "no", "unknown"]
_RANK: dict[str, int] = {"yes": 3, "maybe": 2, "no": 1}
_COLUMNS = "work_id, source_id, state, manga_id, title, author_match, checked_at, via"
_SELECT = f"SELECT {_COLUMNS} FROM availability"


@dataclass(frozen=True)
class Row:
    work_id: int
    source_id: str
    state: State
    manga_id: int | None
    title: str | None
    author_match: bool | None
    checked_at: datetime
    via: str | None = None  # found by searching this title another Chinese source lists it under


def is_fresh(row: Row, settings: Settings, now: datetime) -> bool:
    hit = row.state in ("yes", "maybe")
    days = settings.cache_hit_ttl_days if hit else settings.cache_miss_ttl_days
    return now - row.checked_at <= timedelta(days=days)  # same boundary as the catalogue's SQL


def _row(r: tuple[int, str, State, int | None, str | None, int | None, str, str | None]) -> Row:
    work_id, source_id, state, manga_id, title, author_match, checked_at, via = r
    am = None if author_match is None else bool(author_match)
    return Row(work_id, source_id, state, manga_id, title, am, from_text(checked_at), via)


def rows(con: sqlite3.Connection, work_id: int) -> dict[str, Row]:
    """The work's cached rows, by source."""
    found = con.execute(f"{_SELECT} WHERE work_id = ?", (work_id,))
    return {row.source_id: row for row in map(_row, found)}


def get(con: sqlite3.Connection, work_id: int, source_id: str) -> Row | None:
    r = con.execute(
        f"{_SELECT} WHERE work_id = ? AND source_id = ?", (work_id, source_id)
    ).fetchone()
    return _row(r) if r else None


def listed(
    con: sqlite3.Connection, ids: Iterable[int], sources: Iterable[str], since: datetime
) -> dict[int, list[tuple[str, str, datetime]]]:
    """Work id -> (title, source id, checked at) of its author-confirmed readable rows from
    `sources`, checked since `since` and found by the work's own names (no via), oldest first:
    the titles Chinese sources list it under, which the checker searches on the others."""
    out: defaultdict[int, list[tuple[str, str, datetime]]] = defaultdict(list)
    for wid, title, source_id, at in con.execute(
        "SELECT work_id, title, source_id, checked_at FROM availability"
        " WHERE work_id IN (SELECT value FROM json_each(?))"
        " AND source_id IN (SELECT value FROM json_each(?))"
        " AND author_match = 1 AND state IN ('yes', 'maybe') AND title IS NOT NULL"
        " AND via IS NULL AND checked_at >= ? ORDER BY checked_at, title",
        (json.dumps(list(ids)), json.dumps(list(sources)), to_text(since)),
    ):
        out[wid].append((title, source_id, from_text(at)))
    return dict(out)


def put(con: sqlite3.Connection, row: Row) -> None:
    am = None if row.author_match is None else int(row.author_match)
    con.execute(
        f"INSERT OR REPLACE INTO availability({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (row.work_id, row.source_id, row.state, row.manga_id, row.title, am,
         to_text(row.checked_at), row.via),
    )  # fmt: skip


def readable(rows: Iterable[Row], settings: Settings, now: datetime) -> Readable:
    """Best fresh state across sources (yes > maybe > no); nothing fresh is unknown."""
    best: Readable = "unknown"
    for row in rows:
        if is_fresh(row, settings, now) and _RANK[row.state] > _RANK.get(best, 0):
            best = row.state
    return best
