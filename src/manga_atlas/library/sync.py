"""Mirror the Suwayomi library into state.db, each entry mapped to a Bangumi work."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from manga_atlas.availability import state
from manga_atlas.availability.service import Service
from manga_atlas.config import Settings
from manga_atlas.match import Matcher, catalog
from manga_atlas.suwayomi import LibraryManga


@dataclass(frozen=True)
class Entry:
    manga_id: int
    source_id: str
    title: str
    work_id: int | None
    synced_at: datetime


async def sync(service: Service, *, force: bool = False) -> list[LibraryManga] | None:
    """Refresh the mirror when older than library_sync_ttl_s (or when forced).

    Returns what Suwayomi listed, or None when the mirror was fresh enough to skip.
    Raises SuwayomiError when Suwayomi cannot be read.
    """
    async with service.library_lock:
        loop = asyncio.get_running_loop()
        last = service.library_synced
        if (
            not force
            and last is not None
            and loop.time() - last < service.settings.library_sync_ttl_s
        ):
            return None
        mangas = await service.client.library()
        await asyncio.to_thread(store, service.settings, service.matcher, mangas)
        service.library_synced = loop.time()
        return mangas


def _catalog_built_at(settings: Settings) -> datetime | None:
    try:
        return datetime.fromtimestamp(settings.atlas_db.stat().st_mtime, UTC)
    except FileNotFoundError:
        return None


def store(settings: Settings, matcher: Matcher, mangas: Sequence[LibraryManga]) -> None:
    """Upsert the library rows. Entries are (re)mapped when new, renamed, or older than the
    catalogue; the others keep their mapping."""
    now = state.utc_now()
    built_at = _catalog_built_at(settings)
    with state.connect(settings) as con:
        existing = {
            manga_id: (title, work_id, state.from_text(synced_at))
            for manga_id, title, work_id, synced_at in con.execute(
                "SELECT manga_id, title, work_id, synced_at FROM library"
            )
        }
        stale = [
            m
            for m in mangas
            if (prev := existing.get(m.id)) is None
            or prev[0] != m.title
            or (built_at is not None and built_at >= prev[2])
        ]
        mapped = map_to_works(settings, matcher, stale) if stale else {}
        listed = {m.id for m in mangas}
        con.executemany(
            "DELETE FROM library WHERE manga_id = ?", [(i,) for i in existing if i not in listed]
        )
        con.executemany(
            "INSERT OR REPLACE INTO library VALUES (?, ?, ?, ?, ?)",
            [
                (
                    m.id,
                    m.source_id,
                    m.title,
                    mapped[m.id] if m.id in mapped else existing.get(m.id, ("", None))[1],
                    state.to_text(now),
                )
                for m in mangas
            ],
        )


def map_to_works(
    settings: Settings, matcher: Matcher, mangas: Sequence[LibraryManga]
) -> dict[int, int | None]:
    """Bangumi work per library entry: same title rules as availability (同名不同作 never maps);
    an author match wins, then popularity. Empty when there is no catalogue to map against."""
    try:
        with catalog.connect(settings) as con:
            index = catalog.title_index(con, matcher)
            candidates = {
                m.id: {w for k in matcher.title_keys(m.title) for w in index.get(k, ())}
                for m in mangas
            }
            wanted = set().union(*candidates.values())
            names = catalog.work_names(con, wanted)
            popularity = catalog.popularity(con, wanted)
    except catalog.CatalogMissing:
        return {}
    out: dict[int, int | None] = {}
    for m in mangas:
        best: tuple[bool, int, int] | None = None
        for work_id in candidates[m.id]:
            work = names.get(work_id)
            if work is None:
                continue
            keys = matcher.work_keys(work)
            match = matcher.match_title(m.title, keys)
            author = matcher.author_match(m.creators, keys)
            if match is None or matcher.other_work(match, author):
                continue
            rank = (author is True, popularity.get(work_id, 0), -work_id)
            if best is None or rank > best:
                best = rank
        out[m.id] = -best[2] if best else None
    return out


def entries(settings: Settings) -> list[Entry]:
    with state.connect(settings) as con:
        rows = con.execute(
            "SELECT manga_id, source_id, title, work_id, synced_at FROM library ORDER BY manga_id"
        ).fetchall()
    return [Entry(m, s or "", t or "", w, state.from_text(at)) for m, s, t, w, at in rows]
