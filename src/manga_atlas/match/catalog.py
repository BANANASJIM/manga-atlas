"""Read-only access to atlas.db: the only place matching and availability read the catalogue.

Tables used: work(id, name, name_cn, title, collects), work_alias(work_id, alias),
person(id, name, name_cn), work_person(person_id, work_id, role); for organizing also
tag(id, name), tag_alias(key, tag_id) and work_tag(tag_id, work_id, votes, official, src).
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from manga_atlas.build.schema import CREATOR_ROLES, counted
from manga_atlas.config import Settings
from manga_atlas.match.matcher import Matcher, WorkNames
from manga_atlas.text import key

_CHUNK = 500  # stay well below SQLite's bound-parameter limit
# work_person credits that name creators, not publishers or magazines
_CREATORS = f" AND wp.role IN ({','.join(str(int(r)) for r in sorted(CREATOR_ROLES))})"


class CatalogMissing(RuntimeError):
    """atlas.db is not built yet, or lacks a table this feature needs."""


@contextmanager
def connect(settings: Settings) -> Iterator[sqlite3.Connection]:
    path = settings.atlas_db
    if not path.exists():
        raise CatalogMissing("catalogue not built")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        yield con
    except sqlite3.OperationalError as e:
        raise CatalogMissing(f"catalogue incomplete: {e}") from e
    finally:
        con.close()


def _chunks(ids: Iterable[int]) -> Iterator[list[int]]:
    batch = list(dict.fromkeys(ids))
    for i in range(0, len(batch), _CHUNK):
        yield batch[i : i + _CHUNK]


def _marks(n: int) -> str:
    return ",".join("?" * n)


def work_names(con: sqlite3.Connection, ids: Iterable[int]) -> dict[int, WorkNames]:
    """Names, aliases and creator names for each existing work id."""
    base: dict[int, tuple[str, str]] = {}
    aliases: defaultdict[int, list[str]] = defaultdict(list)
    authors: defaultdict[int, list[str]] = defaultdict(list)
    for chunk in _chunks(ids):
        q = _marks(len(chunk))
        for wid, name, name_cn in con.execute(
            f"SELECT id, name, name_cn FROM work WHERE id IN ({q})", chunk
        ):
            base[wid] = (name, name_cn)
        for wid, alias in con.execute(
            f"SELECT work_id, alias FROM work_alias WHERE work_id IN ({q})", chunk
        ):
            aliases[wid].append(alias)
        for wid, name, name_cn in con.execute(
            "SELECT wp.work_id, p.name, p.name_cn FROM work_person wp"
            f" JOIN person p ON p.id = wp.person_id WHERE wp.work_id IN ({q}){_CREATORS}",
            chunk,
        ):
            authors[wid].extend(n for n in (name, name_cn) if n)
    out = {}
    for wid, (name, name_cn) in base.items():
        primaries = {name, name_cn}
        others = tuple(a for a in dict.fromkeys(aliases[wid]) if a not in primaries)
        out[wid] = WorkNames(wid, name, name_cn, others, tuple(dict.fromkeys(authors[wid])))
    return out


def popular_work_ids(con: sqlite3.Connection, top: int) -> list[int]:
    rows = con.execute("SELECT id FROM work ORDER BY collects DESC, id LIMIT ?", (top,))
    return [wid for (wid,) in rows]


def popularity(con: sqlite3.Connection, ids: Iterable[int]) -> dict[int, int]:
    """Bangumi collection count per existing work id."""
    out: dict[int, int] = {}
    for chunk in _chunks(ids):
        sql = f"SELECT id, collects FROM work WHERE id IN ({_marks(len(chunk))})"
        out.update(con.execute(sql, chunk))
    return out


def title_index(con: sqlite3.Connection, matcher: Matcher) -> dict[str, list[int]]:
    """Every matching key in the catalogue -> work ids, for mapping a library title back to
    Bangumi. Same keys as Matcher.work_keys, so a hit here is an exact or concat match."""
    aliases: defaultdict[int, list[str]] = defaultdict(list)
    for wid, alias in con.execute("SELECT work_id, alias FROM work_alias"):
        aliases[wid].append(alias)
    index: defaultdict[str, list[int]] = defaultdict(list)
    for wid, name, name_cn in con.execute("SELECT id, name, name_cn FROM work"):
        others = tuple(a for a in dict.fromkeys(aliases.pop(wid, ())) if a not in (name, name_cn))
        keys = matcher.work_keys(WorkNames(wid, name, name_cn, others))
        for k in keys.keys | keys.concat:
            index[k].append(wid)
    return dict(index)


def tag_work_ids(con: sqlite3.Connection, tag: str, min_votes: int) -> tuple[str, set[int]] | None:
    """(display name, works carrying the tag: officially, with enough votes or from another
    source), or None."""
    row = con.execute(
        "SELECT t.id, t.name FROM tag_alias a JOIN tag t ON t.id = a.tag_id WHERE a.key = ?"
        " UNION ALL SELECT id, name FROM tag WHERE name = ? LIMIT 1",
        (key(tag), tag),
    ).fetchone()
    if row is None:
        return None
    tag_id, name = row
    works = con.execute(
        f"SELECT work_id FROM work_tag WHERE tag_id = ? AND {counted('?')}", (tag_id, min_votes)
    )
    return name, {wid for (wid,) in works}
