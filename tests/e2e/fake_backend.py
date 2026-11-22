"""Fake MangaAtlas backend for browser tests.

Answers with the same shapes as the real API (query/, availability/, library/, favorites/) over
fixture JSON plus generated filler works, and serves the real static UI the way web/app.py does.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field, replace
from html import escape
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from manga_atlas.build.schema import CREATOR_ROLES, ROLE_LABELS, TagSource
from manga_atlas.config import Settings
from manga_atlas.library.organize import clean_tags
from manga_atlas.suwayomi import manga_url
from manga_atlas.text import norm
from manga_atlas.web.app import STATIC_DIR, WRITE_HEADER

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.json"
_DEFAULTS = Settings()  # what the real server is set to, unless its config says otherwise
MIN_VOTES = _DEFAULTS.tag_min_votes  # user tags below this are ignored, as in the real catalogue
OFFICIAL_VOTES = 9999
CREATOR_LABELS = frozenset(ROLE_LABELS[r] for r in CREATOR_ROLES)  # the roles that make a creator
RANK = {"yes": 3, "maybe": 2, "no": 1}
READABLE = frozenset({"yes", "maybe"})
SUWAYOMI_PATH = "/suwayomi"  # stands in for the real Suwayomi so "read" tabs load
SUGGEST_LIMIT = 6
HEADER_RELATED = 12
HEADER_AUTHORS = 8
ROW_AUTHORS = _DEFAULTS.item_authors_max  # what a result row carries
ROW_TAGS = _DEFAULTS.item_tags_max
ROW_SUMMARY_CHARS = _DEFAULTS.item_summary_chars
ROLE_CODE = {label: int(role) for role, label in ROLE_LABELS.items()}

# Generated works so the home page needs a second page.
FILLER = 70
FILLER_ID = 1001
FILLER_PERSON = 501
FILLER_HEAD = "星海月风夜花雪梦光雨"
FILLER_TAIL = ("之歌", "物语", "旅人", "回廊", "日和", "彼方", "少女", "骑士", "图书馆", "约定")
FILLER_AUTHORS = ("作者甲", "作者乙", "作者丙", "作者丁", "作者戊")
FILLER_TAGS = ("日常", "搞笑", "热血", "奇幻", "恋爱", "悬疑", "科幻", "治愈")
STATUSES = ("连载中", "已完结", "停载")
AUTHOR_MATCH = {"yes": True, "maybe": None, "no": False}  # unless a check spec says otherwise

COVER_COLORS = (
    ("#3a5c9e", "#a9bde4"),
    ("#9e3a4f", "#e6aab7"),
    ("#2f7a57", "#a6d9bd"),
    ("#8a6a1f", "#e8d396"),
    ("#5d3a9e", "#c0ace8"),
    ("#1f6f8a", "#9dd3e3"),
)

Work = dict[str, Any]


@dataclass
class Catalog:
    sources: list[dict[str, str]]
    tags: dict[str, dict[str, Any]]
    people: dict[int, str]
    works: dict[int, Work]
    built_at: str
    total_works: int
    aliases: dict[str, str] = field(default_factory=dict)  # folded spelling -> tag name
    library: dict[str, Any] = field(default_factory=dict)  # the fixture's Suwayomi library


@dataclass
class Shelf:
    """The Suwayomi library: each entry with the work it maps to (or None) and its categories."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    categories: dict[int, str] = field(default_factory=dict)  # id -> name

    @classmethod
    def load(cls, raw: Mapping[str, Any]) -> Shelf:
        return cls(
            [e | {"categories": list(e["categories"])} for e in raw.get("entries", [])],
            {c["id"]: c["name"] for c in raw.get("categories", [])},
        )


def _filler(i: int) -> Work:
    return {
        "id": FILLER_ID + i,
        "title": FILLER_HEAD[i % len(FILLER_HEAD)] + FILLER_TAIL[i // len(FILLER_HEAD)],
        "name": "",
        "aliases": [],
        "year": 1990 + i % 33,
        "status": STATUSES[i % len(STATUSES)],
        "region": "日本",
        "score": round(6 + (i * 7 % 15) / 10, 1),
        "votes": 50 + i,
        "rank": None,
        "heat": 1000 - i,
        "vols": 1 + i % 9,
        "people": [[FILLER_PERSON + i % len(FILLER_AUTHORS), "作者"]],
        "official": [FILLER_TAGS[i % len(FILLER_TAGS)]],
        "user": {FILLER_TAGS[(i + 3) % len(FILLER_TAGS)]: 2 + i % 7},
        "summary": "",
        "cover": False,
    }


def load_catalog(path: Path = FIXTURE) -> Catalog:
    raw = json.loads(path.read_text("utf-8"))
    people = {p["id"]: p["name"] for p in raw["people"]}
    people.update({FILLER_PERSON + k: name for k, name in enumerate(FILLER_AUTHORS)})
    works = {w["id"]: w for w in raw["works"]}
    for i in range(FILLER):
        w = _filler(i)
        works[w["id"]] = w
    tags = {
        t["name"]: {"id": t["id"], "grp": t["grp"], "official": t["official"]} for t in raw["tags"]
    }
    return Catalog(
        raw["sources"],
        tags,
        people,
        works,
        raw["built_at"],
        raw["catalog_works"],
        raw.get("aliases", {}),
        raw.get("library", {}),
    )


@dataclass(frozen=True)
class Params:
    q: str = ""
    t: tuple[str, ...] = ()
    x: tuple[str, ...] = ()
    a: int | None = None
    year_lo: int | None = None
    year_hi: int | None = None
    st: str = ""
    region: str = ""
    min_score: float = 0.0
    read: bool = False
    fav: bool = False
    sort: str = "rank"
    offset: int = 0
    limit: int = 48

    @classmethod
    def parse(cls, qp: Mapping[str, str]) -> Params:
        def split(key: str) -> tuple[str, ...]:
            return tuple(v for v in (qp.get(key) or "").split(",") if v)

        lo, _, hi = (qp.get("y") or "").partition("-")
        return cls(
            q=(qp.get("q") or "").strip(),
            t=split("t"),
            x=split("x"),
            a=int(qp["a"]) if qp.get("a") else None,
            year_lo=int(lo) if lo else None,
            year_hi=int(hi) if hi else None,
            st=qp.get("st") or "",
            region=qp.get("region") or "",
            min_score=float(qp.get("min") or 0),
            read=qp.get("read") == "1",
            fav=qp.get("fav") == "1",
            sort=qp.get("sort") or "rank",
            offset=int(qp.get("offset") or 0),
            limit=int(qp.get("limit") or 48),
        )


@dataclass
class Fake:
    catalog: Catalog
    suwayomi_up: bool = True
    step_s: float = 0.05  # delay before each NDJSON line
    status_delay_s: float = 0.0
    search_delay_s: float = 0.0  # before /api/search answers
    search_max: int = 200  # longest page /api/search returns, as config.py's search_max_limit
    fail_checks: bool = False  # the check stream itself fails (500)
    check_batch_max: int = 100  # works per /api/check, as config.py's check_batch_max
    advertise_limit: bool = True  # /api/status tells check_batch_max
    checked: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    checks: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    batches: list[list[int]] = field(default_factory=list)
    searches: list[Params] = field(default_factory=list)  # as asked, before the cap
    reads: list[int] = field(default_factory=list)  # entries /api/read sent the reader to
    shelf: Shelf = field(default_factory=Shelf)
    organize_delay_s: float = 0.0  # before /api/organize answers
    organizes: list[dict[str, Any]] = field(default_factory=list)  # /api/organize bodies, as sent
    favorites: dict[int, int] = field(default_factory=dict)  # work id -> when added (a counter)
    fail_favorites: bool = False  # adding or removing a favorite fails (500)

    def reset(self) -> None:
        self.suwayomi_up = True
        self.step_s = 0.05
        self.status_delay_s = 0.0
        self.search_delay_s = 0.0
        self.search_max = Fake.search_max
        self.check_batch_max = Fake.check_batch_max
        self.advertise_limit = True
        self.fail_checks = False
        self.checks.clear()
        self.batches.clear()
        self.searches.clear()
        self.reads.clear()
        self.shelf = Shelf.load(self.catalog.library)
        self.organize_delay_s = 0.0
        self.organizes.clear()
        self.favorites.clear()
        self.fail_favorites = False
        self.checked = {  # "stale": cached past the TTL, as the real API marks it fresh:false
            wid: [r | {"fresh": False} if w.get("stale") else r for r in self.outcome(w)]
            for wid, w in self.catalog.works.items()
            if w.get("precheck")
        }

    # ---- catalogue ----

    def tags_of(self, w: Work) -> dict[str, tuple[int, bool]]:
        out = {name: (votes, False) for name, votes in w["user"].items() if votes >= MIN_VOTES}
        out.update({name: (OFFICIAL_VOTES, True) for name in w["official"]})
        for page in source_pages(w).values():  # other sources' tags count without votes
            for name in page["tags"]:
                out.setdefault(name, (w["user"].get(name, 0), False))
        return out

    def sources_of(self, w: Work, name: str) -> list[str]:
        """Where the work's tag comes from, as the work API names the sources."""
        out = []
        if name in w["official"] or w["user"].get(name, 0) > 0:
            out.append(TagSource.BANGUMI.key)
        out += [key for key, page in source_pages(w).items() if name in page["tags"]]
        return out

    def authors_of(self, w: Work) -> list[int]:
        return [pid for pid, role in w["people"] if role in CREATOR_LABELS]

    def outcome(self, w: Work) -> list[dict[str, Any]]:
        """One check line per source from the spec [state, manga_id?, title?, author_match?]."""
        spec = w.get("check") or []
        rows = []
        for i, source in enumerate(self.catalog.sources):
            state, *rest = spec[i] if i < len(spec) else ["no"]
            row: dict[str, Any] = {"source": source["id"], "state": state}
            if rest:
                author = rest[2] if len(rest) > 2 else AUTHOR_MATCH[state]
                row.update(manga_id=rest[0], title=rest[1], author_match=author)
            rows.append(row)
        return rows

    def readable(self, wid: int) -> str:
        rows = self.checked.get(wid)
        if rows is None:
            return "unknown"
        states = [r["state"] for r in rows if r["state"] in RANK and r.get("fresh", True)]
        return max(states, key=RANK.__getitem__) if states else "unknown"

    def text_hit(self, w: Work, q: str) -> bool:
        names = [
            w["title"],
            w["name"],
            *w["aliases"],
            *(self.catalog.people[p] for p, _ in w["people"]),
        ]
        return any(q.lower() in n.lower() for n in names if n)

    def matches(self, w: Work, p: Params, ignore_x: str | None = None) -> bool:
        tags = self.tags_of(w)
        year = w["year"]
        checks = (
            not p.q or self.text_hit(w, p.q),
            all(t in tags for t in p.t),
            not any(t in tags for t in p.x if t != ignore_x),
            p.a is None or p.a in {pid for pid, _ in w["people"]},
            p.year_lo is None or (year is not None and year >= p.year_lo),
            p.year_hi is None or (year is not None and year <= p.year_hi),
            not p.st or w["status"] == p.st,
            not p.region or w["region"] == p.region,
            (w["score"] or 0) >= p.min_score,
            not p.read or self.readable(w["id"]) in READABLE,
            not p.fav or w["id"] in self.favorites,
        )
        return all(checks)

    def sort_key(self, p: Params) -> Callable[[Work], Any]:
        def rel(w: Work) -> int:
            title = w["title"].lower()
            q = p.q.lower()
            return 0 if title == q else 1 if title.startswith(q) else 2

        keys: dict[str, Callable[[Work], Any]] = {
            "heat": lambda w: -w["heat"],
            "score": lambda w: -(w["score"] or 0),
            "date": lambda w: -(w["year"] or 0),
            "rel": lambda w: (rel(w), -w["heat"]),
            # the latest favorite first, then the other works by heat
            "fav": lambda w: (-self.favorites.get(w["id"], 0), -w["heat"]),
        }
        return keys.get(p.sort, lambda w: (w["rank"] is None, w["rank"] or 0, -w["heat"]))

    def item(self, w: Work) -> dict[str, Any]:
        return {
            "id": w["id"],
            "title": w["title"],
            "name": w["name"],
            "name_cn": w["title"],
            "year": w["year"],
            "status": w["status"],
            "region": w["region"],
            "region_inferred": w.get("region_inferred", False),
            "score": w["score"],
            "votes": w["votes"],
            "rank": w["rank"],
            "collects": w["heat"],
            "vols": w["vols"],
            "cover": f"/cover/{w['id']}",
            "readable": self.readable(w["id"]),
            "favorite": w["id"] in self.favorites,
        }

    def group_of(self, name: str) -> str:
        return str(self.catalog.tags.get(name, {}).get("grp", "tag"))

    def by_tag(self, tag: tuple[str, tuple[int, bool]]) -> tuple[bool, int, int]:
        """Official tags first, then by votes, ties by tag id."""
        name, (votes, official) = tag
        return not official, -votes, int(self.catalog.tags[name]["id"])

    def result(self, w: Work) -> dict[str, Any]:
        """A search result: the item plus what a list row shows, ordered and cut as the server
        does (query/search.py)."""
        creators = sorted((p for p in w["people"] if p[1] in CREATOR_LABELS), key=_by_credit)
        people = {pid: {"id": pid, "name": self.catalog.people[pid]} for pid, _ in creators}
        ranked = sorted(self.tags_of(w).items(), key=self.by_tag)
        return self.item(w) | {
            "authors": list(people.values())[:ROW_AUTHORS],
            "tags": [{"name": n, "grp": self.group_of(n)} for n, _ in ranked[:ROW_TAGS]],
            "summary": w["summary"][:ROW_SUMMARY_CHARS],
        }

    def tag_entry(self, name: str, n: int) -> dict[str, Any]:
        return {"name": name, "grp": self.group_of(name), "n": n}

    def canon(self, spelling: str) -> str:
        """The tag a spelling stands for: the real catalogue folds case, script and synonyms."""
        return self.catalog.aliases.get(spelling.casefold(), spelling)

    def fold(self, spellings: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.canon(s) for s in spellings))

    def named_people(self, p: Params) -> list[dict[str, Any]]:
        """The person the results are limited to, with the role they hold in most of their works
        (the lower code on a tie)."""
        works = self.catalog.works.values()
        held = Counter(role for w in works for pid, role in w["people"] if pid == p.a)
        if p.a is None or p.a not in self.catalog.people or not held:
            return []
        role = max(held, key=lambda r: (held[r], -ROLE_CODE[r]))
        name = self.catalog.people[p.a]
        return [{"id": p.a, "name": name, "role": role, "creator": role in CREATOR_LABELS}]

    def facets(self, hits: list[Work], p: Params, asked: Params) -> dict[str, Any]:
        """`p` names tags as the catalogue knows them, `asked` as the page spelled them."""
        chosen = {*p.t, *p.x}  # selected tags are not listed, as in the real API
        counts = Counter(name for w in hits for name in self.tags_of(w) if name not in chosen)
        removed = {  # how many works each exclusion removes on its own
            name: sum(
                1
                for w in self.catalog.works.values()
                if name in self.tags_of(w) and self.matches(w, p, ignore_x=name)
            )
            for name in p.x
        }

        def values(key: str) -> list[dict[str, Any]]:
            return [
                {"name": k, "n": n}
                for k, n in Counter(w[key] for w in hits if w[key]).most_common()
            ]

        return {
            "tags": [self.tag_entry(name, n) for name, n in counts.most_common()],
            "region": values("region"),
            "status": values("status"),
            "included": [
                {"query": s, "name": self.canon(s), "grp": self.group_of(self.canon(s))}
                for s in dict.fromkeys(asked.t)
                if self.canon(s) in self.catalog.tags
            ],
            "excluded": [
                {"query": s, **self.tag_entry(self.canon(s), removed[self.canon(s)])}
                for s in dict.fromkeys(asked.x)
                if self.canon(s) in self.catalog.tags
            ],
            "people": self.named_people(p),
        }

    def tag_header(self, name: str) -> dict[str, Any]:
        works = [w for w in self.catalog.works.values() if name in self.tags_of(w)]
        related = Counter(t for w in works for t in self.tags_of(w) if t != name)
        authors = Counter(pid for w in works for pid in self.authors_of(w))
        return {
            "tag": self.tag_entry(name, len(works)),
            "related": [self.tag_entry(t, n) for t, n in related.most_common(HEADER_RELATED)],
            "people": [
                {"id": pid, "name": self.catalog.people[pid], "name_cn": "", "n": n}
                for pid, n in authors.most_common(HEADER_AUTHORS)
            ],
        }

    def search(self, asked: Params) -> dict[str, Any]:
        p = replace(asked, t=self.fold(asked.t), x=self.fold(asked.x))
        hits = sorted(
            (w for w in self.catalog.works.values() if self.matches(w, p)), key=self.sort_key(p)
        )
        out: dict[str, Any] = {
            "total": len(hits),
            "items": [self.result(w) for w in hits[p.offset : p.offset + p.limit]],
            "facets": self.facets(hits, p, asked),
        }
        if len(p.t) == 1 and not p.q:
            out["tag_header"] = self.tag_header(p.t[0])
        return out

    def suggest(self, q: str) -> dict[str, Any]:
        needle = q.lower()
        if not needle:
            return {"tags": [], "works": [], "people": []}
        per_tag = Counter(t for w in self.catalog.works.values() for t in self.tags_of(w))
        tags = sorted(
            (name for name in self.catalog.tags if needle in name.lower()),
            key=lambda name: (name.lower() != needle, -per_tag[name]),
        )
        works = sorted(
            (w for w in self.catalog.works.values() if self.text_hit(w, q) and w["id"] < FILLER_ID),
            key=lambda w: -w["heat"],
        )
        per_person = Counter(pid for w in self.catalog.works.values() for pid, _ in w["people"])
        people = [pid for pid, name in self.catalog.people.items() if needle in name.lower()]
        return {
            "tags": [self.tag_entry(t, per_tag[t]) for t in tags[:SUGGEST_LIMIT]],
            "works": [
                {k: self.item(w)[k] for k in ("id", "title", "name", "year", "cover")}
                for w in works[:SUGGEST_LIMIT]
            ],
            "people": [
                {"id": pid, "name": self.catalog.people[pid], "name_cn": "", "works": n}
                for pid in people[:SUGGEST_LIMIT]
                if (n := per_person[pid])
            ],
        }

    def relation(self, r: dict[str, Any]) -> dict[str, Any]:
        target = self.catalog.works.get(r["id"])
        if target is None:
            return {
                "relation": r["rel"],
                "id": r["id"],
                "title": r["title"],
                "year": None,
                "type": r["type"],
                "in_catalog": False,
            }
        return {
            "relation": r["rel"],
            "id": target["id"],
            "title": target["title"],
            "year": target["year"],
            "type": "书籍",
            "in_catalog": True,
        }

    def detail(self, wid: int) -> dict[str, Any]:
        w = self.catalog.works.get(wid)
        if w is None:
            raise HTTPException(status_code=404)
        return self.item(w) | {
            "kind": "series",
            "aliases": w["aliases"],
            "summary": w["summary"],
            "people": [
                {
                    "id": pid,
                    "name": self.catalog.people[pid],
                    "name_cn": "",
                    "role": role,
                    "creator": role in CREATOR_LABELS,
                    "linked": True,
                }
                for pid, role in w["people"]
            ],
            "tags": [
                {
                    "name": n,
                    "grp": self.group_of(n),
                    "votes": v,
                    "official": off,
                    "weak": False,
                    "sources": self.sources_of(w, n),
                }
                for n, (v, off) in self.tags_of(w).items()
            ],
            "pages": [
                {"source": key, "title": page["title"], "url": page["url"]}
                for key, page in source_pages(w).items()
            ],
            "relations": [self.relation(r) for r in w.get("relations", [])],
            "availability": [
                {"checked_at": self.catalog.built_at, "fresh": True} | r
                for r in self.checked.get(wid, [])
            ],
        }

    # ---- 整理书架 ----

    def organize(self, tags: list[str], apply: bool) -> dict[str, Any]:
        """As library/organize.py: a category per tag, named as the catalogue names the tag, for
        the entries whose work carries it. Apply makes the missing categories and only adds."""
        shelf = self.shelf
        named = {norm(name): cid for cid, name in shelf.categories.items()}
        held = {e["manga_id"]: tuple(e["categories"]) for e in shelf.entries}  # before applying
        groups: dict[str, dict[str, Any]] = {}
        for tag in clean_tags(tags):
            canon = self.canon(tag)
            known = canon in self.catalog.tags
            name = canon if known else tag
            group = groups.setdefault(
                norm(name),
                {
                    "tag": tag,
                    "name": name,
                    "known": known,
                    "id": named.get(norm(name)),
                    "members": [],
                },
            )
            group["members"] += [
                e
                for e in shelf.entries
                if known
                and e["work_id"] in self.catalog.works
                and canon in self.tags_of(self.catalog.works[e["work_id"]])
                and e not in group["members"]
            ]
        created: list[str] = []
        added = 0
        for group in groups.values() if apply else ():
            if not group["members"]:
                continue
            if group["id"] is None:
                group["id"] = max(shelf.categories, default=0) + 1
                shelf.categories[group["id"]] = group["name"]
                created.append(group["name"])
            todo = [e for e in group["members"] if group["id"] not in e["categories"]]
            for e in todo:
                e["categories"].append(group["id"])
            added += len(todo)

        def member(e: dict[str, Any], cid: int | None) -> dict[str, Any]:
            assigned = cid is not None and (apply or cid in held[e["manga_id"]])
            return {k: e[k] for k in ("manga_id", "title", "work_id")} | {"assigned": assigned}

        return {
            "apply": apply,
            "categories": [
                {
                    "tag": g["tag"],
                    "name": g["name"],
                    "known": g["known"],
                    "category_id": g["id"],
                    "exists": g["id"] is not None and g["name"] not in created,
                    "mangas": [member(e, g["id"]) for e in g["members"]],
                }
                for g in groups.values()
            ],
            "unmapped": [
                {"manga_id": e["manga_id"], "title": e["title"]}
                for e in shelf.entries
                if e["work_id"] is None
            ],
            "created": created,
            "added": added,
        }

    # ---- availability streams ----

    async def check_stream(self, wid: int) -> AsyncIterator[bytes]:
        rows = self.outcome(self.catalog.works[wid])
        cached = {r["source"]: r for r in self.checked.get(wid, []) if r.get("fresh", True)}
        for row in rows:
            await asyncio.sleep(self.step_s)
            down = {"source": row["source"], "state": "down"}
            # Like the real checker, a cached verdict needs no Suwayomi.
            yield _line(row if self.suwayomi_up else cached.get(row["source"], down))
        if self.suwayomi_up:
            self.checked[wid] = [r for r in rows if r["state"] != "down"]

    async def batch_stream(self, ids: list[int]) -> AsyncIterator[bytes]:
        for done, wid in enumerate(ids, 1):
            await asyncio.sleep(self.step_s)
            w = self.catalog.works.get(wid)
            if w is not None and self.suwayomi_up:
                self.checked[wid] = [r for r in self.outcome(w) if r["state"] != "down"]
            yield _line(
                {"id": wid, "readable": self.readable(wid), "done": done, "total": len(ids)}
            )


def source_pages(w: Work) -> dict[str, dict[str, Any]]:
    """The work's page at each other tag source, by source key in TagSource order, with the tags
    it gives the work as the catalogue names them."""
    order = [s.key for s in TagSource]
    pages: dict[str, dict[str, Any]] = w.get("pages") or {}
    return {key: pages[key] for key in sorted(pages, key=order.index)}


def _by_credit(credit: list[Any]) -> tuple[int, bool, int]:
    """The work page's order: by role, linked people (positive ids) before infobox-only names."""
    pid, role = credit
    return ROLE_CODE[role], pid < 0, abs(pid)


def _line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


def cover_svg(w: Work) -> str:
    dark, light = COVER_COLORS[w["id"] % len(COVER_COLORS)]
    size = min(26, 210 // max(1, len(w["title"])))
    text = 'fill="#fff" text-anchor="middle" font-family="Noto Sans CJK SC, sans-serif"'
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 250 350">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{light}"/><stop offset="1" stop-color="{dark}"/>'
        '</linearGradient></defs><rect width="250" height="350" fill="url(#g)"/>'
        '<rect x="14" y="14" width="222" height="322" fill="none" stroke="#fff" '
        'stroke-opacity=".5" stroke-width="2"/>'
        f'<text x="125" y="250" font-size="{size}" font-weight="700" {text}>'
        f"{escape(w['title'])}</text>"
        f'<text x="125" y="282" font-size="13" fill-opacity=".8" {text}>'
        f"{escape(w['name'])}</text></svg>"
    )


def suwayomi_url(request: Request) -> str:
    """Where the fake Suwayomi answers: beside the fake backend itself."""
    return str(request.base_url).rstrip("/") + SUWAYOMI_PATH


def create_app(fake: Fake) -> FastAPI:
    app = FastAPI()
    works = fake.catalog.works

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Any]) -> Response:
        if request.method not in ("GET", "HEAD") and request.headers.get(WRITE_HEADER) != "1":
            return JSONResponse({"error": "missing X-Atlas header"}, status_code=403)
        response: Response = await call_next(request)
        return response

    @app.get("/api/search")
    async def api_search(request: Request) -> dict[str, Any]:
        asked = Params.parse(request.query_params)
        fake.searches.append(asked)
        await asyncio.sleep(fake.search_delay_s)
        return fake.search(replace(asked, limit=min(asked.limit, fake.search_max)))

    @app.get("/api/suggest")
    def api_suggest(q: str = "") -> dict[str, Any]:
        return fake.suggest(q.strip())

    @app.get("/api/work/{wid}")
    def api_work(wid: int) -> dict[str, Any]:
        return fake.detail(wid)

    @app.post("/api/work/{wid}/check")
    async def api_check(wid: int, request: Request) -> StreamingResponse:
        if wid not in works:
            raise HTTPException(status_code=404)
        if fake.fail_checks:
            raise HTTPException(status_code=500)
        fake.checks.append((wid, json.loads(await request.body() or b"{}")))
        return StreamingResponse(fake.check_stream(wid), media_type="application/x-ndjson")

    @app.post("/api/check", response_model=None)
    async def api_check_many(request: Request) -> StreamingResponse | JSONResponse:
        ids = [int(i) for i in (await request.json()).get("ids", [])]
        if not ids or len(set(ids)) > fake.check_batch_max:
            return JSONResponse({"error": f"send 1-{fake.check_batch_max} ids"}, status_code=422)
        fake.batches.append(ids)
        return StreamingResponse(fake.batch_stream(ids), media_type="application/x-ndjson")

    @app.get("/api/status")
    async def api_status(request: Request) -> dict[str, Any]:
        await asyncio.sleep(fake.status_delay_s)
        limit = {"check_batch_max": fake.check_batch_max} if fake.advertise_limit else {}
        return limit | {
            "suwayomi_url": suwayomi_url(request),
            "reachable": fake.suwayomi_up,
            "version": "fake" if fake.suwayomi_up else None,
            "sources": [
                {"lang": "zh", **s, "installed": True, "breaker": "closed", "retry_in_s": 0.0}
                for s in fake.catalog.sources
            ],
            "catalog": {
                "built_at": fake.catalog.built_at,
                "works": fake.catalog.total_works,
                "dump_id": "fixture",
            },
        }

    @app.post("/api/organize", response_model=None)
    async def api_organize(request: Request) -> dict[str, Any] | JSONResponse:
        body = await request.json()
        fake.organizes.append(body)
        await asyncio.sleep(fake.organize_delay_s)
        if not clean_tags(body.get("tags") or []):
            return JSONResponse({"error": "no tags"}, status_code=422)
        if not fake.suwayomi_up:  # the real one reads the library first
            return JSONResponse({"error": "Suwayomi unreachable"}, status_code=503)
        return fake.organize(body["tags"], bool(body.get("apply")))

    @app.put("/api/favorites/{wid}", status_code=204)
    def api_favorite(wid: int) -> None:
        if wid not in works:
            raise HTTPException(status_code=404)
        if fake.fail_favorites:
            raise HTTPException(status_code=500)
        fake.favorites.setdefault(wid, max(fake.favorites.values(), default=0) + 1)

    @app.delete("/api/favorites/{wid}", status_code=204)
    def api_unfavorite(wid: int) -> None:
        if fake.fail_favorites:
            raise HTTPException(status_code=500)
        fake.favorites.pop(wid, None)

    @app.get("/api/read/{manga_id}")
    def api_read(manga_id: int, request: Request) -> RedirectResponse:
        fake.reads.append(manga_id)
        return RedirectResponse(manga_url(suwayomi_url(request), manga_id), status_code=302)

    @app.get("/cover/{wid}")
    def cover(wid: int) -> Response:
        w = works.get(wid)
        if w is None or not w.get("cover"):
            raise HTTPException(status_code=404)
        return Response(cover_svg(w), media_type="image/svg+xml")

    @app.get(SUWAYOMI_PATH + "/{path:path}")
    def suwayomi(path: str) -> HTMLResponse:
        return HTMLResponse(f"<!doctype html><title>Suwayomi</title><p>{escape(path)}</p>")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        return FileResponse(STATIC_DIR / "index.html")

    return app


class LiveServer:
    """Runs an ASGI app with uvicorn on a free port in a background thread."""

    def __init__(self, app: FastAPI) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> str:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("fake backend did not start")
            time.sleep(0.01)
        port = self._server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)
