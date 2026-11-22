"""Write atlas.db in one go; the caller swaps it into place."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from manga_atlas.build.schema import (
    DDL,
    INDEXES,
    RELATION_LABELS,
    ROLE_LABELS,
    STATUS_LABELS,
    SUBJECT_TYPE_LABELS,
    TAG_SOURCE_LABELS,
    Kind,
)

# A throwaway file is being written: no journal, no fsync per transaction, generous cache.
_BUILD_PRAGMAS = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
PRAGMA temp_store = MEMORY;
PRAGMA cache_size = -262144;
"""
WorkRow = tuple[
    int, int, str, str, str, str | None, int | None, int, str | None, int,
    float | None, int, int | None, int, int, int,
]  # fmt: skip


@dataclass
class Rows:
    meta: dict[str, str]
    works: list[WorkRow] = field(default_factory=list)
    texts: list[tuple[int, str]] = field(default_factory=list)
    aliases: list[tuple[int, str]] = field(default_factory=list)
    work_keys: list[tuple[int, str, str]] = field(default_factory=list)
    tags: list[tuple[int, str, str, str]] = field(default_factory=list)
    tag_aliases: list[tuple[str, int]] = field(default_factory=list)
    work_tags: list[tuple[int, int, int, int, int]] = field(default_factory=list)
    work_pages: list[tuple[int, int, str, str]] = field(default_factory=list)
    persons: list[tuple[int, str, str, int, int]] = field(default_factory=list)
    person_aliases: list[tuple[int, str]] = field(default_factory=list)
    person_keys: list[tuple[int, str]] = field(default_factory=list)
    work_persons: list[tuple[int, int, int]] = field(default_factory=list)
    relations: list[tuple[int, int, int]] = field(default_factory=list)
    targets: list[tuple[int, int, str, str]] = field(default_factory=list)


def _labels() -> Iterator[tuple[str, int, str]]:
    yield from (("kind", k.value, k.name.lower()) for k in Kind)
    yield from (("status", s.value, label) for s, label in STATUS_LABELS.items())
    yield from (("role", r.value, label) for r, label in ROLE_LABELS.items())
    yield from (("relation", code, label) for code, label in RELATION_LABELS.items())
    yield from (("subject_type", code, label) for code, label in SUBJECT_TYPE_LABELS.items())
    yield from (("tag_source", s.value, label) for s, label in TAG_SOURCE_LABELS.items())


def write(path: Path, rows: Rows) -> None:
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(_BUILD_PRAGMAS + DDL)
        with con:
            con.executemany("INSERT INTO meta VALUES (?, ?)", sorted(rows.meta.items()))
            con.executemany("INSERT INTO label VALUES (?, ?, ?)", _labels())
            con.executemany(f"INSERT INTO work VALUES ({', '.join('?' * 16)})", rows.works)
            con.executemany("INSERT INTO work_text VALUES (?, ?)", rows.texts)
            con.executemany("INSERT INTO work_alias VALUES (?, ?)", rows.aliases)
            con.executemany("INSERT INTO work_key VALUES (?, ?, ?)", rows.work_keys)
            con.executemany("INSERT INTO tag VALUES (?, ?, ?, ?)", rows.tags)
            con.executemany("INSERT INTO tag_alias VALUES (?, ?)", rows.tag_aliases)
            con.executemany("INSERT INTO work_tag VALUES (?, ?, ?, ?, ?)", rows.work_tags)
            con.executemany("INSERT INTO work_page VALUES (?, ?, ?, ?)", rows.work_pages)
            con.executemany("INSERT INTO person VALUES (?, ?, ?, ?, ?)", rows.persons)
            con.executemany("INSERT INTO person_alias VALUES (?, ?)", rows.person_aliases)
            con.executemany("INSERT INTO person_key VALUES (?, ?)", rows.person_keys)
            con.executemany("INSERT INTO work_person VALUES (?, ?, ?)", rows.work_persons)
            con.executemany("INSERT INTO relation VALUES (?, ?, ?)", rows.relations)
            con.executemany("INSERT INTO target VALUES (?, ?, ?, ?)", rows.targets)
        con.executescript(INDEXES)
        with con:
            for fts in ("work_fts", "person_fts"):
                con.execute(f"INSERT INTO {fts}({fts}) VALUES ('rebuild')")
                con.execute(f"INSERT INTO {fts}({fts}) VALUES ('optimize')")
        con.execute("ANALYZE")
    finally:
        con.close()
    with path.open("rb+") as fh:
        os.fsync(fh.fileno())
