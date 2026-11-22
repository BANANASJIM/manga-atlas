"""Work page: everything the catalogue knows about one work, plus cached availability."""

from __future__ import annotations

from typing import Any

from manga_atlas.build.schema import (
    CREATOR_ROLES,
    RELATION_LABELS,
    ROLE_LABELS,
    STATUS_LABELS,
    SUBJECT_TYPE_LABELS,
    Kind,
    Role,
    Status,
    TagSource,
    counted,
)
from manga_atlas.config import Settings
from manga_atlas.query.db import Handle
from manga_atlas.query.search import FRESH_RANK, READABLE, enabled_sources, favorite_ids

BOOK = 1  # subject type of every work
_FIELDS = (
    "id", "kind", "title", "name", "name_cn", "date", "year", "status", "region",
    "region_inferred", "score", "votes", "rank", "collects", "vols",
)  # fmt: skip


def work(h: Handle, settings: Settings, work_id: int) -> dict[str, Any] | None:
    con = h.con
    row = con.execute(f"SELECT {', '.join(_FIELDS)} FROM work WHERE id = ?", (work_id,)).fetchone()
    if row is None:
        return None
    out: dict[str, Any] = dict(zip(_FIELDS, row, strict=True))
    out["kind"] = Kind(out["kind"]).name.lower()
    out["status"] = STATUS_LABELS.get(Status(out["status"]))
    out["region_inferred"] = bool(out["region_inferred"])
    out["cover"] = f"/cover/{work_id}"
    shown = {out["title"], out["name"], out["name_cn"]}
    out["aliases"] = [
        a
        for (a,) in con.execute("SELECT alias FROM work_alias WHERE work_id = ?", (work_id,))
        if a not in shown
    ]
    summary = con.execute("SELECT summary FROM work_text WHERE work_id = ?", (work_id,)).fetchone()
    out["summary"] = summary[0] if summary else ""
    out["people"] = [
        {
            "id": pid,
            "name": name,
            "name_cn": name_cn,
            "role": ROLE_LABELS[Role(role)],
            "creator": Role(role) in CREATOR_ROLES,  # false: a publisher or magazine
            "linked": pid > 0,  # has a Bangumi person page; else a name from the infobox
        }
        for pid, name, name_cn, role in con.execute(
            "SELECT p.id, p.name, p.name_cn, wp.role FROM work_person wp"
            " JOIN person p ON p.id = wp.person_id WHERE wp.work_id = ?"
            " ORDER BY wp.role, p.id < 0, abs(p.id)",
            (work_id,),
        )
    ]
    out["tags"] = [
        {
            "name": name,
            "grp": grp,
            "votes": votes,
            "official": bool(official),
            "weak": not count,  # too few votes: not among the work's tags in search
            "sources": [s.key for s in TagSource(src)],
        }
        for name, grp, votes, official, src, count in con.execute(
            f"SELECT t.name, t.grp, wt.votes, wt.official, wt.src, {counted('?', 'wt')}"
            " FROM work_tag wt JOIN tag t ON t.id = wt.tag_id WHERE wt.work_id = ?"
            " ORDER BY wt.official DESC, wt.votes DESC, t.id",
            (settings.tag_min_votes, work_id),
        )
    ]
    out["pages"] = [  # where the tags of each other source come from
        {"source": TagSource(src).key, "title": title, "url": url}
        for src, title, url in con.execute(
            "SELECT src, title, url FROM work_page WHERE work_id = ? ORDER BY src", (work_id,)
        )
    ]
    out["relations"] = [
        {
            "relation": RELATION_LABELS.get(rel, str(rel)),
            "id": target,
            "title": title or other_cn or other_name,
            "year": year,
            "type": SUBJECT_TYPE_LABELS.get(BOOK if title else kind, ""),
            "in_catalog": title is not None,
        }
        for rel, target, title, year, kind, other_name, other_cn in con.execute(
            "SELECT r.rel, r.target_id, w.title, w.year, t.type, t.name, t.name_cn"
            " FROM relation r LEFT JOIN work w ON w.id = r.target_id"
            " LEFT JOIN target t ON t.id = r.target_id WHERE r.work_id = ?"
            " ORDER BY r.rel, w.title IS NULL, r.target_id",
            (work_id,),
        )
    ]
    out["availability"], out["readable"] = _availability(h, settings, work_id)
    out["favorite"] = work_id in favorite_ids(h, [work_id])
    return out


def _availability(h: Handle, settings: Settings, work_id: int) -> tuple[list[dict[str, Any]], str]:
    if not h.state:
        return [], READABLE[0]
    order = {s.id: i for i, s in enumerate(settings.sources)}
    rows = h.con.execute(
        f"SELECT source_id, state, manga_id, title, author_match, checked_at, {FRESH_RANK}"
        " FROM st.availability WHERE work_id = :work"
        " AND source_id IN (SELECT value FROM json_each(:sources))",
        {
            "work": work_id,
            "hit": settings.cache_hit_ttl_days,
            "miss": settings.cache_miss_ttl_days,
            "sources": enabled_sources(settings),
        },
    ).fetchall()
    rows.sort(key=lambda r: (order.get(str(r[0]), len(order)), str(r[0])))
    cached = [
        {
            "source": source,
            "state": state,
            "manga_id": manga_id,
            "title": title,
            "author_match": None if author is None else bool(author),
            "checked_at": checked_at,
            "fresh": rank > 0,
        }
        for source, state, manga_id, title, author, checked_at, rank in rows
    ]
    best = max((r[-1] for r in rows), default=0)
    return cached, READABLE[best]
