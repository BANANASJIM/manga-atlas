"""Browse and search: text, tags, people and filters, with facet counts for the current results.

The matching works go into the connection's temp table `hits` once; totals, the page, facets and
the tag overview are all read from it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from typing import Any

from manga_atlas.build import vocab as vocab_rules
from manga_atlas.build.schema import (
    CREATOR_ROLES,
    ROLE_LABELS,
    STATUS_LABELS,
    Role,
    Status,
    counted,
)
from manga_atlas.config import Settings
from manga_atlas.query.db import Catalog, Handle
from manga_atlas.text import key

TRIGRAM = 3  # FTS5 trigram MATCH needs this many characters; shorter terms scan with LIKE
SORTS = ("rel", "rank", "heat", "score", "date", "fav")
_ORDER = {  # {t}: table alias; "rel" without text falls back to heat
    "rel": "{t}.tier, {t}.collects DESC, {t}.id",
    "heat": "{t}.collects DESC, {t}.id",
    "rank": "{t}.rank_key, {t}.collects DESC, {t}.id",
    "score": "{t}.votes >= :score_votes DESC, {t}.score DESC NULLS LAST, {t}.votes DESC, {t}.id",
    "date": "{t}.date DESC NULLS LAST, {t}.id",
}
ITEM_FIELDS = (
    "id", "title", "name", "name_cn", "year", "status", "region", "region_inferred", "score",
    "votes", "rank", "collects", "vols",
)  # fmt: skip
_ITEM = ", ".join(f"w.{f}" for f in ITEM_FIELDS)
_HIT_COLUMNS = "w.region, w.status, w.collects, w.rank_key, w.score, w.votes, w.date"
_CREATORS = ", ".join(str(int(r)) for r in sorted(CREATOR_ROLES))
_COUNT_SPARSE = (  # {rows}: which rows of temp.hits h to count
    "SELECT wt.tag_id, count(*) FROM temp.hits h CROSS JOIN work_tag wt ON wt.work_id = h.id"
    f" WHERE {{rows}} AND {counted('?', 'wt')} GROUP BY wt.tag_id"
)
_COUNT_DENSE = (
    f"SELECT tag_id, count(*) FROM work_tag NOT INDEXED WHERE {counted('?')}"
    " AND EXISTS (SELECT 1 FROM temp.hits h WHERE h.id = work_tag.work_id AND {rows})"
    " GROUP BY tag_id"
)
READABLE = ("unknown", "no", "maybe", "yes")  # worst to best
# Above this share of the catalogue, counting tags by scanning work_tag in tag order beats
# joining from the hits (which needs a sort); measured to cross over between 56% and 76%.
_DENSE = 1 / 2
# When filters alone keep more than this share of the catalogue, work from what they drop.
_MOSTLY = 1 / 2
# Rank of one availability row: its state while fresh, else unknown (0).
FRESH_RANK = (
    "CASE WHEN state = 'yes' AND julianday('now') - julianday(checked_at) <= :hit THEN 3"
    " WHEN state = 'maybe' AND julianday('now') - julianday(checked_at) <= :hit THEN 2"
    " WHEN state = 'no' AND julianday('now') - julianday(checked_at) <= :miss THEN 1"
    " ELSE 0 END"
)


@dataclass(frozen=True)
class Query:
    text: str = ""
    include: tuple[str, ...] = ()  # tag names
    exclude: tuple[str, ...] = ()
    people: tuple[int, ...] = ()  # person ids
    year_from: int | None = None
    year_to: int | None = None
    statuses: tuple[str, ...] = ()  # 连载中 / 已完结 / 停载
    regions: tuple[str, ...] = ()
    min_score: float | None = None
    readable: bool = False  # only works with a fresh yes/maybe in state.db
    favorite: bool = False  # only the favorites in state.db
    sort: str = "rel"
    offset: int = 0
    limit: int | None = None  # the page size; None: settings.search_limit


def _order(sort: str, t: str, ranked: bool = False) -> str:
    """ORDER BY terms of `sort` over table alias `t`; "rel" ranks text matches, so without any
    (`ranked` false) it is heat."""
    return _ORDER["heat" if sort == "rel" and not ranked else sort].format(t=t)


def _page(q: Query, frm: str, t: str, where: str, ranked: bool = False) -> str:
    """The page of ids among the candidates (FROM `frm` as `t`, WHERE `where`) in the asked order.
    "fav" comes in two parts, so heat walks its index and stops at the page end instead of sorting
    every candidate: the favorites by when they were added, then the rest."""
    if q.sort != "fav":
        return (
            f"SELECT {t}.id FROM {frm} WHERE {where}"
            f" ORDER BY {_order(q.sort, t, ranked)} LIMIT :limit OFFSET :offset"
        )
    return (
        f"SELECT id FROM (SELECT {t}.id AS id, 0 AS part, f.added_at AS added, {t}.collects AS heat"
        f" FROM {frm} JOIN st.favorite f ON f.work_id = {t}.id WHERE {where}"
        " UNION ALL SELECT id, 1, NULL, heat FROM ("
        f"SELECT {t}.id AS id, {t}.collects AS heat FROM {frm}"
        f" WHERE {where} AND {t}.id NOT IN (SELECT work_id FROM st.favorite)"
        f" ORDER BY {_order('heat', t)} LIMIT :limit + :offset))"
        " ORDER BY part, added DESC, heat DESC, id LIMIT :limit OFFSET :offset"
    )


def _page_args(settings: Settings, q: Query) -> dict[str, int]:
    limit = settings.search_limit if q.limit is None else q.limit
    return {"limit": limit, "offset": q.offset, "score_votes": settings.score_min_votes}


def enabled_sources(settings: Settings) -> str:
    """The sources availability is read from, as a JSON list for json_each: verdicts of a
    disabled source neither show nor count."""
    return json.dumps([s.id for s in settings.enabled_sources])


def parse_years(text: str) -> tuple[int | None, int | None]:
    """'2010-2020', '2010-', '-2000' or '2015'. Raises ValueError otherwise."""
    lo, sep, hi = text.strip().partition("-")
    start = int(lo) if lo.strip() else None
    end = int(hi) if hi.strip() else None
    return (start, end) if sep else (start, start)


def status_code(label: str) -> int | None:
    """连载中 / 已完结 / 停载, or a spelling that folds into one (完结)."""
    rules = vocab_rules.load()
    status = rules.status(rules.aliases.get(key(label), key(label)))
    return None if status is None else int(status)


def region_name(label: str) -> str:
    """The stored region for a label or a spelling that folds into one (日漫 -> 日本)."""
    rules = vocab_rules.load()
    k = rules.aliases.get(key(label), key(label))
    return rules.regions.get(k, label.strip())


@dataclass
class Sql:
    args: dict[str, Any] = field(default_factory=dict)

    def bind(self, value: Any) -> str:
        name = f"p{len(self.args)}"
        self.args[name] = value
        return ":" + name


def _phrase(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@dataclass(frozen=True)
class TextIndex:
    fts: str
    table: str  # external content of `fts`
    columns: tuple[str, ...]
    names: str  # column whose lines rank a match: exact, prefix, inside


WORK_TEXT = TextIndex("work_fts", "work_key", ("title", "people"), "title")
PERSON_TEXT = TextIndex("person_fts", "person_key", ("names",), "names")


def text_match(
    sql: Sql, terms: list[str], index: TextIndex, columns: tuple[str, ...]
) -> tuple[str, list[str], str]:
    """FROM clause (rows of `index.table` as k), conditions and a tier expression, such that
    every term occurs in `columns`: FTS5 phrases for 3+ characters, LIKE scans for shorter."""
    long_terms = [t for t in terms if len(t) >= TRIGRAM]
    source = f"{index.table} k"
    if long_terms:
        phrases = " ".join(_phrase(t) for t in long_terms)
        if columns != index.columns:
            phrases = "{" + " ".join(columns) + "} : (" + phrases + ")"
        source = (
            f"(SELECT rowid AS id FROM {index.fts} WHERE {index.fts} MATCH {sql.bind(phrases)}) m"
            f" CROSS JOIN {index.table} k ON k.id = m.id"
        )
    conds = []
    for t in terms:
        if len(t) < TRIGRAM:
            pat = sql.bind(_like(t))
            conds.append("(" + " OR ".join(f"k.{c} LIKE {pat} ESCAPE '\\'" for c in columns) + ")")
    q, nl, col = sql.bind(" ".join(terms)), "char(10)", f"k.{index.names}"
    tier = (  # exact name, name prefix, anything else
        f"CASE WHEN instr({nl} || {col} || {nl}, {nl} || {q} || {nl}) THEN 0"
        f" WHEN instr({nl} || {col}, {nl} || {q}) THEN 1 ELSE 2 END"
    )
    return source, conds, tier


@dataclass
class Plan:
    terms: list[str]
    include: list[int]
    exclude: list[int]
    sql: Sql
    source: str = "work w"  # FROM clause yielding the candidate works as w
    tier: str = "0"
    conds: list[str] = field(default_factory=list)
    excluded: str = "0"  # true for works carrying an excluded tag
    narrowed: bool = False  # by text, tags, people or favorites: few works, collected directly


def plan(
    settings: Settings,
    q: Query,
    include: list[int | None],
    exclude: list[int],
    state: bool,
) -> Plan | None:
    """SQL pieces for the matching works, given the tags to include (None: one the catalogue
    lacks) and to exclude, and whether state.db can be read; None when nothing can match."""
    statuses = [status_code(s) for s in q.statuses if s.strip()]
    if None in include or None in statuses or ((q.readable or q.favorite) and not state):
        return None
    sql = Sql()
    found = Plan(key(q.text).split(), [t for t in include if t is not None], [], sql)
    found.narrowed = bool(found.terms or found.include or q.people or q.favorite)
    if found.terms:
        found.source, found.conds, found.tier = text_match(
            sql, found.terms, WORK_TEXT, WORK_TEXT.columns
        )
        found.source += " CROSS JOIN work w ON w.id = k.id"
    conds = found.conds
    tagged = counted(sql.bind(settings.tag_min_votes))
    if found.include:
        posting = " INTERSECT ".join(
            f"SELECT work_id FROM work_tag WHERE tag_id = {sql.bind(t)} AND {tagged}"
            for t in dict.fromkeys(found.include)
        )
        conds.append(f"w.id IN ({posting})")
    if q.people:
        posting = " INTERSECT ".join(
            f"SELECT work_id FROM work_person WHERE person_id = {sql.bind(p)}"
            for p in dict.fromkeys(q.people)
        )
        conds.append(f"w.id IN ({posting})")
    if q.year_from is not None:
        conds.append(f"w.year >= {sql.bind(q.year_from)}")
    if q.year_to is not None:
        conds.append(f"w.year <= {sql.bind(q.year_to)}")
    if statuses:
        conds.append(f"w.status IN ({', '.join(sql.bind(s) for s in statuses)})")
    regions = [region_name(r) for r in q.regions if r.strip()]
    if regions:
        conds.append(f"w.region IN ({', '.join(sql.bind(r) for r in regions)})")
    if q.min_score is not None:
        conds.append(f"w.score >= {sql.bind(q.min_score)}")
    if q.readable:
        sources = sql.bind(enabled_sources(settings))
        conds.append(
            "w.id IN (SELECT work_id FROM st.availability WHERE state IN ('yes', 'maybe')"
            f" AND source_id IN (SELECT value FROM json_each({sources}))"
            " AND julianday('now') - julianday(checked_at)"
            f" <= {sql.bind(settings.cache_hit_ttl_days)})"
        )
    if q.favorite:
        conds.append("w.id IN (SELECT work_id FROM st.favorite)")
    found.exclude = list(dict.fromkeys(exclude))
    if found.exclude:
        ids = ", ".join(sql.bind(t) for t in found.exclude)
        found.excluded = (
            f"w.id IN (SELECT work_id FROM work_tag WHERE tag_id IN ({ids}) AND {tagged})"
        )
    return found


def search(h: Handle, settings: Settings, q: Query) -> dict[str, Any]:
    cat = h.catalog
    if q.sort == "fav" and not h.state:
        q = replace(q, sort="heat")  # no favorites to put first: what "fav" orders the rest by
    # What the condition chips need is known from the query alone, so it is told even when
    # nothing matches.
    asked = _resolve(cat, q.include)
    included = {n: t for n, t in asked if t is not None}
    excluded = {n: t for n, t in _resolve(cat, q.exclude) if t is not None}
    people = _people(h, q.people)
    found = plan(settings, q, [t for _, t in asked], list(excluded.values()), h.state)
    if found is None:
        chips = _chips(cat, included, excluded, {}, people)
        return {"total": 0, "items": [], "facets": _facets(h, settings, {}, {}, {}, set(), chips)}
    if not (found.terms or found.conds or found.exclude):
        return _browse_all(h, settings, q)
    con, args = h.con, found.sql.args
    where = " AND ".join(found.conds) or "1"
    con.execute("DELETE FROM temp.hits")
    dropped = -1
    if not found.narrowed:
        # Filters only: what they drop (or an excluded tag removes) may be the smaller set.
        outside = " OR ".join(
            ([f"({where}) IS NOT 1"] if found.conds else [])
            + ([found.excluded] if found.exclude else [])
        )
        dropped = con.execute(f"SELECT count(*) FROM work w WHERE {outside}", args).fetchone()[0]
    if 0 <= dropped < (1 - _MOSTLY) * h.catalog.works:
        # temp.hits holds the dropped works; kept marks those that pass the filters.
        con.execute(
            f"INSERT INTO temp.hits SELECT w.id, 0, ({where}) IS 1, {_HIT_COLUMNS}"
            f" FROM work w WHERE {outside}",
            args,
        )
        total = h.catalog.works - dropped
        page_sql = _page(q, "work w", "w", "w.id NOT IN (SELECT id FROM temp.hits)")
        all_tags, all_places, all_statuses = _whole(h, settings)
        cut_tags, cut_places, cut_statuses = _counts(h, settings, dropped, "1")
        counts = _minus(all_tags, cut_tags)
        places = _minus(all_places, cut_places)
        statuses = _minus(all_statuses, cut_statuses)
        candidates = "kept"  # rows an excluded tag removed
    else:
        kept = f"NOT {found.excluded}" if found.exclude else "1"
        con.execute(
            f"INSERT INTO temp.hits SELECT w.id, {found.tier}, {kept}, {_HIT_COLUMNS}"
            f" FROM {found.source} WHERE {where}",
            args,
        )
        total = con.execute("SELECT count(*) FROM temp.hits WHERE kept").fetchone()[0]
        page_sql = _page(q, "temp.hits h", "h", "h.kept", bool(found.terms))
        counts, places, statuses = _counts(h, settings, total, "h.kept")
        candidates = "1"
    page = [wid for (wid,) in con.execute(page_sql, _page_args(settings, q))]
    chosen = {*found.include, *found.exclude}
    removed = _removed(h, settings, found.exclude, candidates)
    chips = _chips(cat, included, excluded, removed, people)
    result: dict[str, Any] = {
        "total": total,
        "items": items(h, settings, page),
        "facets": _facets(h, settings, counts, places, statuses, chosen, chips),
    }
    if len(found.include) == 1 and not found.terms:
        result["tag_header"] = _tag_header(h, settings, found.include[0], counts, total, chosen)
    return result


Counts = tuple[dict[int, int], dict[str, int], dict[str, int]]  # tags, regions, statuses


def _minus[K](whole: dict[K, int], part: dict[K, int]) -> dict[K, int]:
    return {k: v - part.get(k, 0) for k, v in whole.items()}


def _counts(h: Handle, settings: Settings, n: int, rows: str) -> Counts:
    """Facet counts over the `rows` of temp.hits h (n of them)."""
    query = _COUNT_DENSE if n > _DENSE * h.catalog.works else _COUNT_SPARSE
    tags = dict(h.con.execute(query.format(rows=rows), (settings.tag_min_votes,)))
    return (tags, *_place_status(h, f"FROM temp.hits h WHERE {rows}"))


def _whole(h: Handle, settings: Settings) -> Counts:
    """Facet counts over the whole catalogue, computed once per build."""
    cat = h.catalog
    whole = cat.overview.get(settings.tag_min_votes)
    if whole is None:
        tags = cat.tag_counts(h.con, settings.tag_min_votes)
        whole = cat.overview[settings.tag_min_votes] = (tags, *_place_status(h, "FROM work"))
    return whole


def _facets(
    h: Handle,
    settings: Settings,
    counts: dict[int, int],
    places: dict[str, int],
    statuses: dict[str, int],
    chosen: set[int],
    chips: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """The counts the panel lists, then the `chips` the query itself asks for."""
    ranked = sorted(
        ((n, t) for t, n in counts.items() if n and t not in chosen), key=lambda nt: -nt[0]
    )  # most works first; equal counts keep their order
    return {
        "tags": [_tag(h.catalog, t, n) for n, t in ranked[: settings.facet_tag_limit]],
        "region": _ranked(places),
        "status": _ranked(statuses),
        **chips,
    }


def _browse_all(h: Handle, settings: Settings, q: Query) -> dict[str, Any]:
    """No conditions, so no chips either: the whole catalogue in index order."""
    page = [
        wid
        for (wid,) in h.con.execute(
            _page(q, "work w", "w", "1"),
            _page_args(settings, q),
        )
    ]
    chips = _chips(h.catalog, {}, {}, {}, [])
    return {
        "total": h.catalog.works,
        "items": items(h, settings, page),
        "facets": _facets(h, settings, *_whole(h, settings), set(), chips),
    }


def _place_status(h: Handle, source: str) -> tuple[dict[str, int], dict[str, int]]:
    """Counts per region and status in one pass (a GROUP BY would sort every row)."""
    regions, statuses = h.catalog.regions, list(STATUS_LABELS)
    sql = Sql()
    columns = [f"count(*) FILTER (WHERE region = {sql.bind(r)})" for r in regions]
    columns += [f"count(*) FILTER (WHERE status = {sql.bind(int(s))})" for s in statuses]
    row = h.con.execute(f"SELECT {', '.join(columns)} {source}", sql.args).fetchone()
    places = dict(zip(regions, row[: len(regions)], strict=True))
    labels = dict(zip((STATUS_LABELS[s] for s in statuses), row[len(regions) :], strict=True))
    return places, labels


def _ranked(counts: dict[str, int]) -> list[dict[str, Any]]:
    return [{"name": k, "n": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1]) if v]


def _tag(cat: Catalog, tag_id: int, n: int) -> dict[str, Any]:
    info = cat.tags[tag_id]
    return {"name": info.name, "grp": info.group, "n": n}


def _resolve(cat: Catalog, names: tuple[str, ...]) -> list[tuple[str, int | None]]:
    """Each spelling asked and the tag it stands for (None: the catalogue has none). The page
    knows its chips by the spelling, whatever the catalogue folds it into."""
    return [(n, cat.aliases.get(key(n))) for n in names if n.strip()]


def _chips(
    cat: Catalog,
    included: dict[str, int],
    excluded: dict[str, int],
    removed: dict[int, int],
    people: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """What the query itself asks for, as the page's condition chips."""
    return {
        "included": _included(cat, included),
        "excluded": _excluded(cat, excluded, removed),
        "people": people,
    }


def _included(cat: Catalog, spelled: dict[str, int]) -> list[dict[str, Any]]:
    """The tags the results are limited to, each as the spelling asked, with its tag's name and
    group. Chosen tags are not among the facet tags, so this is how their chips find a group."""
    return [
        {"query": s, "name": cat.tags[t].name, "grp": cat.tags[t].group} for s, t in spelled.items()
    ]


def _excluded(
    cat: Catalog, spelled: dict[str, int], removed: dict[int, int]
) -> list[dict[str, Any]]:
    """Each excluded tag as the spelling asked, with how many results it removes (`removed`, by
    tag; none when nothing matched)."""
    return [{"query": s, **_tag(cat, t, removed.get(t, 0))} for s, t in spelled.items()]


def _people(h: Handle, ids: tuple[int, ...]) -> list[dict[str, Any]]:
    """The people the results are limited to, each with the role they hold in most of their works
    (the lower code on a tie), so a chip can say "出版社 講談社" rather than "作者 講談社".
    Unknown ids are left out."""
    asked = list(dict.fromkeys(ids))
    names: dict[int, str] = {}
    held: dict[int, dict[int, int]] = {}  # person -> role -> works
    if asked:
        for pid, name, role, n in h.con.execute(
            "SELECT p.id, p.name, wp.role, count(*) FROM person p"
            " JOIN work_person wp ON wp.person_id = p.id"
            " WHERE p.id IN (SELECT value FROM json_each(?)) GROUP BY p.id, wp.role",
            (json.dumps(asked),),
        ):
            names[pid] = name
            held.setdefault(pid, {})[role] = n
    out = []
    for pid in asked:
        if pid in held:
            main = Role(max(held[pid].items(), key=lambda kv: (kv[1], -kv[0]))[0])
            out.append(
                {
                    "id": pid,
                    "name": names[pid],
                    "role": ROLE_LABELS[main],
                    "creator": main in CREATOR_ROLES,
                }
            )
    return out


def _removed(h: Handle, settings: Settings, exclude: list[int], candidates: str) -> dict[int, int]:
    """How many results each excluded tag removes on its own ("恐怖 -7"), counted among the
    `candidates` rows of temp.hits."""
    removed = {}
    for tag in exclude:
        sql = Sql()
        tagged = counted(sql.bind(settings.tag_min_votes))
        query = (
            f"SELECT count(*) FROM temp.hits WHERE {candidates} AND id IN (SELECT work_id"
            f" FROM work_tag WHERE tag_id = {sql.bind(tag)} AND {tagged})"
        )
        others = [sql.bind(t) for t in exclude if t != tag]
        if others:
            query += (
                " AND id NOT IN (SELECT work_id FROM work_tag"
                f" WHERE tag_id IN ({', '.join(others)}) AND {tagged})"
            )
        removed[tag] = h.con.execute(query, sql.args).fetchone()[0]
    return removed


def _tag_header(
    h: Handle, settings: Settings, tag: int, counts: dict[int, int], total: int, chosen: set[int]
) -> dict[str, Any]:
    """Overview of one selected tag: the tags that go with it, and who draws it most."""
    cat = h.catalog
    everywhere = cat.tag_counts(h.con, settings.tag_min_votes)
    scored = []
    for other, together in counts.items():
        if other in chosen or not everywhere.get(other):
            continue
        lift = together * cat.works / (total * everywhere[other])
        if lift > 1:
            scored.append((together * math.log(lift), other, together))
    scored.sort(key=lambda s: -s[0])
    top = h.con.execute(
        "SELECT wp.person_id, count(DISTINCT wp.work_id) AS n FROM temp.hits h"
        " CROSS JOIN work_person wp ON wp.work_id = h.id"
        f" WHERE h.kept AND wp.role IN ({_CREATORS})"
        " GROUP BY wp.person_id ORDER BY n DESC, wp.person_id LIMIT ?",
        (settings.tag_header_people,),
    ).fetchall()
    names = {
        pid: (name, name_cn)
        for pid, name, name_cn in h.con.execute(
            "SELECT id, name, name_cn FROM person WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps([pid for pid, _ in top]),),
        )
    }
    return {
        "tag": _tag(cat, tag, everywhere.get(tag, 0)),
        "related": [_tag(cat, t, n) for _, t, n in scored[: settings.tag_header_tags]],
        "people": [
            {"id": pid, "name": names[pid][0], "name_cn": names[pid][1], "n": n} for pid, n in top
        ],
    }


def items(h: Handle, settings: Settings, ids: list[int]) -> list[dict[str, Any]]:
    """Result cards for `ids`, in that order."""
    if not ids:
        return []
    rows = {
        r[0]: r
        for r in h.con.execute(
            f"SELECT {_ITEM} FROM work w WHERE w.id IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        )
    }
    readable = readability(h, settings, ids)
    favorites = favorite_ids(h, ids)
    authors = _item_authors(h, settings, ids)
    tags = _item_tags(h, settings, ids)
    summaries = _item_summaries(h, settings, ids)
    out = []
    for wid in ids:
        item = dict(zip(ITEM_FIELDS, rows[wid], strict=True))
        item["status"] = STATUS_LABELS.get(Status(item["status"]))
        item["region_inferred"] = bool(item["region_inferred"])
        item["cover"] = f"/cover/{wid}"
        item["readable"] = readable.get(wid, READABLE[0])
        item["favorite"] = wid in favorites
        item["authors"] = authors.get(wid, [])
        item["tags"] = tags.get(wid, [])
        item["summary"] = summaries.get(wid, "")
        out.append(item)
    return out


def _item_authors(h: Handle, settings: Settings, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Each work's creators in the work page's order (role, then linked before infobox-only
    names), a person once however many roles they hold, at most `item_authors_max`."""
    out: dict[int, list[dict[str, Any]]] = {}
    rows = h.con.execute(
        "SELECT wp.work_id, p.id, p.name FROM work_person wp JOIN person p ON p.id = wp.person_id"
        f" WHERE wp.work_id IN (SELECT value FROM json_each(?)) AND wp.role IN ({_CREATORS})"
        " ORDER BY wp.work_id, wp.role, p.id < 0, abs(p.id)",
        (json.dumps(ids),),
    )
    for wid, pid, name in rows:
        people = out.setdefault(wid, [])
        if len(people) < settings.item_authors_max and all(p["id"] != pid for p in people):
            people.append({"id": pid, "name": name})
    return out


def _item_tags(h: Handle, settings: Settings, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Each work's tags the facets count (official, enough votes, or from another source):
    official first, then by votes, at most `item_tags_max`."""
    out: dict[int, list[dict[str, Any]]] = {}
    rows = h.con.execute(
        "SELECT wt.work_id, t.name, t.grp FROM work_tag wt JOIN tag t ON t.id = wt.tag_id"
        f" WHERE wt.work_id IN (SELECT value FROM json_each(?)) AND {counted('?', 'wt')}"
        " ORDER BY wt.work_id, wt.official DESC, wt.votes DESC, t.id",
        (json.dumps(ids), settings.tag_min_votes),
    )
    for wid, name, grp in rows:
        tags = out.setdefault(wid, [])
        if len(tags) < settings.item_tags_max:
            tags.append({"name": name, "grp": grp})
    return out


def _item_summaries(h: Handle, settings: Settings, ids: list[int]) -> dict[int, str]:
    """The first `item_summary_chars` characters of each work's summary."""
    return dict(
        h.con.execute(
            "SELECT work_id, substr(summary, 1, ?) FROM work_text"
            " WHERE work_id IN (SELECT value FROM json_each(?))",
            (settings.item_summary_chars, json.dumps(ids)),
        )
    )


def readability(h: Handle, settings: Settings, ids: list[int]) -> dict[int, str]:
    """Best fresh state per work across sources; stale rows count as unknown."""
    if not h.state or not ids:
        return {}
    rows = h.con.execute(
        f"SELECT work_id, max({FRESH_RANK}) FROM st.availability"
        " WHERE work_id IN (SELECT value FROM json_each(:ids))"
        " AND source_id IN (SELECT value FROM json_each(:sources)) GROUP BY work_id",
        {
            "hit": settings.cache_hit_ttl_days,
            "miss": settings.cache_miss_ttl_days,
            "ids": json.dumps(ids),
            "sources": enabled_sources(settings),
        },
    )
    return {wid: READABLE[best] for wid, best in rows}


def favorite_ids(h: Handle, ids: list[int]) -> set[int]:
    """Which of `ids` are favorites."""
    if not h.state or not ids:
        return set()
    rows = h.con.execute(
        "SELECT work_id FROM st.favorite WHERE work_id IN (SELECT value FROM json_each(?))",
        (json.dumps(ids),),
    )
    return {wid for (wid,) in rows}
