"""Read-only access to atlas.db, with state.db attached as `st` once it exists."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from manga_atlas.build.schema import SCHEMA_VERSION, counted
from manga_atlas.config import Settings

# Map the whole file so a fresh connection reads straight from the OS page cache.
_MMAP_BYTES = 1 << 28
# Works matching the current request, with the columns that facets and sorting need.
# tier ranks text matches; kept is 0 when an excluded tag removes the work.
_HITS = """CREATE TEMP TABLE hits(
  id INTEGER PRIMARY KEY, tier INTEGER NOT NULL, kept INTEGER NOT NULL, region TEXT,
  status INTEGER NOT NULL, collects INTEGER NOT NULL, rank_key INTEGER NOT NULL, score REAL,
  votes INTEGER NOT NULL, date TEXT)"""


class CatalogMissing(RuntimeError):
    """atlas.db has not been built yet, or not by this version."""


@dataclass(frozen=True)
class TagInfo:
    id: int
    name: str
    group: str


@dataclass
class Catalog:
    """What one atlas.db build holds that every request needs; shared by all threads."""

    tags: dict[int, TagInfo]
    aliases: dict[str, int]  # normalized spelling -> tag id
    works: int
    regions: list[str]
    counts: dict[int, dict[int, int]] = field(default_factory=dict)  # min votes -> tag -> works
    # min votes -> facet counts (tags, regions, statuses) over the whole catalogue
    overview: dict[int, tuple[dict[int, int], dict[str, int], dict[str, int]]] = field(
        default_factory=dict
    )

    @classmethod
    def load(cls, con: sqlite3.Connection) -> Catalog:
        tags = {i: TagInfo(i, n, g) for i, n, g in con.execute("SELECT id, name, grp FROM tag")}
        aliases = dict(con.execute("SELECT key, tag_id FROM tag_alias"))
        works = con.execute("SELECT count(*) FROM work").fetchone()[0]
        regions = [r for (r,) in con.execute("SELECT DISTINCT region FROM work WHERE region > ''")]
        return cls(tags, aliases, works, sorted(regions))

    def tag_counts(self, con: sqlite3.Connection, min_votes: int) -> dict[int, int]:
        if min_votes not in self.counts:
            self.counts[min_votes] = dict(
                con.execute(
                    f"SELECT tag_id, count(*) FROM work_tag WHERE {counted('?')} GROUP BY tag_id",
                    (min_votes,),
                )
            )
        return self.counts[min_votes]


@dataclass
class Handle:
    con: sqlite3.Connection
    path: Path
    ident: tuple[int, int]  # inode and mtime of atlas.db when opened
    catalog: Catalog
    state_path: Path | None = None  # state.db attached as `st`
    state: bool = False  # st.availability can be read


_local = threading.local()
_catalogs: dict[tuple[Path, tuple[int, int]], Catalog] = {}
_catalogs_lock = threading.Lock()


def _catalog(path: Path, ident: tuple[int, int], con: sqlite3.Connection) -> Catalog:
    with _catalogs_lock:
        cat = _catalogs.get((path, ident))
        if cat is None:
            for old in [k for k in _catalogs if k[0] == path]:
                del _catalogs[old]  # a previous build
            cat = _catalogs[(path, ident)] = Catalog.load(con)
        return cat


def open_catalog(settings: Settings) -> Handle:
    """This thread's connection, reopened when a rebuild has replaced atlas.db."""
    path = settings.atlas_db
    try:
        st = path.stat()
    except FileNotFoundError:
        raise CatalogMissing(f"{path} not built yet; run: manga-atlas build") from None
    ident = (st.st_ino, st.st_mtime_ns)
    handle: Handle | None = getattr(_local, "handle", None)
    if handle is None or handle.path != path or handle.ident != ident:
        if handle is not None:
            handle.con.close()
            _local.handle = None
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
        if _schema(con) != str(SCHEMA_VERSION):
            con.close()
            raise CatalogMissing(f"{path} is from an older version; run: manga-atlas build --force")
        con.execute(f"PRAGMA mmap_size = {_MMAP_BYTES}")
        con.execute("PRAGMA temp_store = MEMORY")
        con.execute(_HITS)
        handle = _local.handle = Handle(con, path, ident, _catalog(path, ident, con))
    handle.state = _attach_state(handle, settings.state_db)
    return handle


def _schema(con: sqlite3.Connection) -> str | None:
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row else None


def _attach_state(handle: Handle, path: Path) -> bool:
    con = handle.con
    if handle.state_path is not None and handle.state_path != path:
        con.execute("DETACH DATABASE st")
        handle.state_path = None
    if handle.state_path is None:
        if not path.exists():
            return False
        try:
            con.execute("ATTACH DATABASE ? AS st", (f"file:{path}?mode=ro",))
        except sqlite3.Error:
            return False
        handle.state_path = path
    try:
        found = con.execute(
            "SELECT 1 FROM st.sqlite_master WHERE type = 'table' AND name = 'availability'"
        ).fetchone()
    except sqlite3.Error:
        return False
    return found is not None


def catalog_info(settings: Settings) -> dict[str, object]:
    """Build time, source dump and work count of atlas.db, for /api/status."""
    info: dict[str, object] = {"built_at": None, "dump_id": None, "works": None}
    if not settings.atlas_db.exists():
        return info
    try:
        con = sqlite3.connect(f"file:{settings.atlas_db}?mode=ro", uri=True)
        try:
            meta = dict(con.execute("SELECT key, value FROM meta"))
        finally:
            con.close()
    except sqlite3.Error:
        return info
    works = meta.get("works")
    return {
        "built_at": meta.get("built_at"),
        "dump_id": meta.get("dump_id"),
        "works": int(works) if works else None,
    }
