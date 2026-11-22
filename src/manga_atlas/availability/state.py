"""state.db: the app's own tables (availability cache, library mapping, favorites).
Catalogue queries attach it read-only."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from manga_atlas.config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS availability(
  work_id INTEGER, source_id TEXT,
  state TEXT CHECK(state IN ('yes','maybe','no')),
  manga_id INTEGER, title TEXT, author_match INTEGER,
  checked_at TEXT, via TEXT,
  PRIMARY KEY(work_id, source_id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS library(
  manga_id INTEGER PRIMARY KEY, source_id TEXT, title TEXT, work_id INTEGER, synced_at TEXT);
CREATE TABLE IF NOT EXISTS favorite(work_id INTEGER PRIMARY KEY, added_at TEXT NOT NULL);
"""

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # UTC, second precision: sorts and compares as text


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TIME_FORMAT)


def from_text(text: str) -> datetime:
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=UTC)


@contextmanager
def connect(settings: Settings) -> Iterator[sqlite3.Connection]:
    """Open state.db (created on first use); commits on success."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.state_db)
    try:
        con.execute("PRAGMA journal_mode=WAL")  # catalogue queries read while we write
        con.executescript(SCHEMA)
        with con:
            yield con
    finally:
        con.close()
