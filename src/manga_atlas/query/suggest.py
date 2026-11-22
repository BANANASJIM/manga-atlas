"""Search-box dropdown: tags, works and people matching what has been typed."""

from __future__ import annotations

from typing import Any

from manga_atlas.config import Settings
from manga_atlas.query.db import Handle
from manga_atlas.query.search import PERSON_TEXT, WORK_TEXT, Sql, text_match
from manga_atlas.text import key

# Search-box syntax: "#tag" and "-#tag" ask for tags only, "作者:" for people only.
TAG_PREFIXES = ("-#", "#")
PERSON_PREFIXES = ("作者:", "作者：")


def suggest(h: Handle, settings: Settings, typed: str) -> dict[str, list[dict[str, Any]]]:
    text, groups = typed.strip(), {"tags", "works", "people"}
    for prefix in TAG_PREFIXES:
        if text.startswith(prefix):
            text, groups = text.removeprefix(prefix), {"tags"}
            break
    for prefix in PERSON_PREFIXES:
        if text.startswith(prefix):
            text, groups = text.removeprefix(prefix), {"people"}
            break
    terms = key(text).split()
    limit = settings.suggest_limit
    return {
        "tags": _tags(h, settings, " ".join(terms), limit) if terms and "tags" in groups else [],
        "works": _works(h, terms, limit) if terms and "works" in groups else [],
        "people": _people(h, terms, limit) if terms and "people" in groups else [],
    }


def _tags(h: Handle, settings: Settings, typed: str, limit: int) -> list[dict[str, Any]]:
    """Tags whose name or any folded spelling (GL -> 百合) contains the input."""
    cat = h.catalog
    counts = cat.tag_counts(h.con, settings.tag_min_votes)
    best: dict[int, int] = {}
    for spelling, tag in cat.aliases.items():
        if typed in spelling:
            tier = 0 if spelling == typed else 1 if spelling.startswith(typed) else 2
            best[tag] = min(tier, best.get(tag, tier))
    ranked = sorted(best, key=lambda t: (best[t], -counts.get(t, 0), t))[:limit]
    return [
        {"name": cat.tags[t].name, "grp": cat.tags[t].group, "n": counts.get(t, 0)} for t in ranked
    ]


def _works(h: Handle, terms: list[str], limit: int) -> list[dict[str, Any]]:
    sql = Sql()
    source, conds, tier = text_match(sql, terms, WORK_TEXT, ("title",))
    rows = h.con.execute(
        f"SELECT w.id, w.title, w.name, w.year FROM {source} CROSS JOIN work w ON w.id = k.id"
        f" WHERE {' AND '.join(conds) or '1'} ORDER BY {tier}, w.collects DESC, w.id"
        f" LIMIT {sql.bind(limit)}",
        sql.args,
    )
    return [
        {"id": i, "title": title, "name": name, "year": year, "cover": f"/cover/{i}"}
        for i, title, name, year in rows
    ]


def _people(h: Handle, terms: list[str], limit: int) -> list[dict[str, Any]]:
    sql = Sql()
    source, conds, tier = text_match(sql, terms, PERSON_TEXT, PERSON_TEXT.columns)
    rows = h.con.execute(
        f"SELECT p.id, p.name, p.name_cn, p.works FROM {source} CROSS JOIN person p ON p.id = k.id"
        f" WHERE {' AND '.join(conds) or '1'} ORDER BY {tier}, p.works DESC, p.id"
        f" LIMIT {sql.bind(limit)}",
        sql.args,
    )
    return [{"id": i, "name": n, "name_cn": cn, "works": w} for i, n, cn, w in rows]
