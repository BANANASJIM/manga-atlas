"""Works from the Archive: manga series plus popular standalone volumes, volumes folded into their
series, adult works left out."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from manga_atlas.build import infobox
from manga_atlas.build.infobox import Infobox
from manga_atlas.build.schema import Kind, Status
from manga_atlas.build.vocab import Vocab
from manga_atlas.config import Settings

BOOK = 1  # subject type
COMIC = 1001  # book platform 漫画
REL_SERIES = 1002  # volume -> its series
REL_VOLUME = 1003  # series -> one of its volumes
YEAR_MIN, YEAR_MAX = 1800, 2100  # anything else in a date field is a typo
_YEAR = re.compile(r"\d{4}")
_VOLS = re.compile(r"^(\d+)|(\d+)\s*[卷巻册冊本]")  # "14（2025-08）", "既刊565卷(…)"


@dataclass(slots=True)
class Subject:
    id: int
    series: bool
    nsfw: bool
    name: str
    name_cn: str
    date: str
    score: float
    votes: int
    rank: int
    collects: int
    tags: list[tuple[str, int]]
    meta: list[str]
    infobox: str  # kept only for subjects that may become works
    summary: str


@dataclass(slots=True)
class Other:
    """A subject that is not manga, kept so relations can name it."""

    type: int
    name: str
    name_cn: str
    nsfw: bool


@dataclass(slots=True)
class Tally:
    user: dict[str, int]  # canonical tag -> votes
    official: list[str]  # canonical tags in Bangumi's order


def read_subjects(
    rows: Iterable[dict[str, Any]], detail_min_collects: int
) -> tuple[dict[int, Subject], dict[int, Other]]:
    comics: dict[int, Subject] = {}
    others: dict[int, Other] = {}
    for d in rows:
        if d["type"] != BOOK or d["platform"] != COMIC:
            others[d["id"]] = Other(d["type"], d["name"], d["name_cn"], bool(d["nsfw"]))
            continue
        collects = sum((d.get("favorite") or {}).values())
        detail = bool(d["series"]) or collects >= detail_min_collects
        comics[d["id"]] = Subject(
            id=d["id"],
            series=bool(d["series"]),
            nsfw=bool(d["nsfw"]),
            name=d["name"],
            name_cn=d["name_cn"],
            date=d.get("date") or "",
            score=float(d.get("score") or 0),
            votes=sum((d.get("score_details") or {}).values()),
            rank=int(d.get("rank") or 0),
            collects=collects,
            tags=[(t["name"], int(t["count"])) for t in d.get("tags") or ()],
            meta=list(d.get("meta_tags") or ()),
            infobox=d["infobox"] if detail else "",
            summary=d["summary"] if detail else "",
        )
    return comics, others


def read_relations(
    rows: Iterable[dict[str, Any]], comics: dict[int, Subject]
) -> tuple[dict[int, int], list[tuple[int, int, int]]]:
    """Volume -> series (the lowest id when several series claim one), and all other edges."""
    parents: dict[int, set[int]] = defaultdict(set)
    edges: list[tuple[int, int, int]] = []
    for r in rows:
        a = comics.get(r["subject_id"])
        if a is None:
            continue
        rel, b_id = r["relation_type"], r["related_subject_id"]
        if rel not in (REL_SERIES, REL_VOLUME):
            edges.append((a.id, rel, b_id))
            continue
        b = comics.get(b_id)
        if b is None:
            continue
        if rel == REL_SERIES and not a.series and b.series:
            parents[a.id].add(b.id)
        elif rel == REL_VOLUME and a.series and not b.series:
            parents[b.id].add(a.id)
    return {v: min(ps) for v, ps in parents.items()}, edges


class Tagger:
    """Folds a subject's tags into canonical tags and decides whether it is adult."""

    def __init__(self, vocab: Vocab, known: set[str], settings: Settings) -> None:
        self.vocab = vocab
        self.known = known
        self._canon: dict[str, str] = {}
        self._r18_user = {self.canon(t) for t in settings.r18_user_tags}
        self._r18_official = {self.canon(t) for t in vocab.r18_official}
        self._r18_min_votes = settings.r18_min_votes

    @classmethod
    def for_subjects(cls, subjects: Iterable[Subject], vocab: Vocab, settings: Settings) -> Tagger:
        known: set[str] = set()
        for s in subjects:
            known.update(vocab.key(name) for name, _ in s.tags)
            known.update(vocab.key(m) for m in s.meta)
        return cls(vocab, known, settings)

    def canon(self, raw: str) -> str:
        hit = self._canon.get(raw)
        if hit is None:
            hit = self._canon[raw] = self.vocab.canonical(self.vocab.key(raw), self.known)
        return hit

    def tally(self, s: Subject) -> Tally:
        user: dict[str, int] = {}
        for name, count in s.tags:
            key = self.canon(name)
            user[key] = user.get(key, 0) + count
        return Tally(user, list(dict.fromkeys(self.canon(m) for m in s.meta)))

    def adult(self, s: Subject, t: Tally | None = None) -> bool:
        if s.nsfw:
            return True
        t = t or self.tally(s)
        return any(k in self._r18_official for k in t.official) or any(
            t.user.get(k, 0) >= self._r18_min_votes for k in self._r18_user
        )


@dataclass
class Selection:
    works: dict[int, Subject]
    kinds: dict[int, Kind]
    tallies: dict[int, Tally]
    children: dict[int, list[Subject]]  # series -> its folded volumes
    parent: dict[int, int]  # volume -> series
    hidden: set[int]  # adult manga subjects: never shown, never linked
    stats: Counter[str] = field(default_factory=Counter)


def select(
    comics: dict[int, Subject], parent: dict[int, int], tagger: Tagger, settings: Settings
) -> Selection:
    sel = Selection({}, {}, {}, defaultdict(list), parent, set())
    for s in comics.values():
        if s.series:
            sel.stats["series"] += 1
            kind = Kind.SERIES
        else:
            sel.stats["volumes"] += 1
            if s.id in parent:
                continue
            sel.stats["standalone"] += 1
            if s.collects < settings.standalone_min_collects:
                continue
            kind = Kind.VOLUME
        t = tagger.tally(s)
        if tagger.adult(s, t):
            sel.hidden.add(s.id)
            sel.stats["adult_series" if s.series else "adult_standalone"] += 1
            continue
        sel.works[s.id], sel.kinds[s.id], sel.tallies[s.id] = s, kind, t
    for volume, series in parent.items():
        if series in sel.works:
            sel.children[series].append(comics[volume])
        else:
            sel.hidden.add(volume)
    sel.stats["works"] = len(sel.works)
    sel.stats["standalone_kept"] = sum(1 for k in sel.kinds.values() if k is Kind.VOLUME)
    return sel


def relations(
    edges: Iterable[tuple[int, int, int]],
    sel: Selection,
    comics: dict[int, Subject],
    others: dict[int, Other],
    tagger: Tagger,
) -> tuple[set[tuple[int, int, int]], dict[int, tuple[int, str, str]]]:
    """Relations between works (volumes folded), and the non-work subjects they point at."""
    rows: set[tuple[int, int, int]] = set()
    targets: dict[int, tuple[int, str, str]] = {}
    for a, rel, b in edges:
        source = sel.parent.get(a, a)
        if source not in sel.works:
            continue
        if b in comics:
            dest = sel.parent.get(b, b)
            if dest == source or dest in sel.hidden:
                continue
            if dest not in sel.works:
                s = comics[dest]
                if tagger.adult(s):
                    continue
                targets[dest] = (BOOK, s.name, s.name_cn)
        else:
            other = others.get(b)
            if other is None or other.nsfw:
                continue
            dest = b
            targets[dest] = (other.type, other.name, other.name_cn)
        rows.add((source, rel, dest))
    return rows, targets


def titles(s: Subject, box: Infobox) -> list[str]:
    """Original, Chinese, alias and edition titles, spelled as on Bangumi."""
    found = (s.name, s.name_cn, *infobox.values(box, "中文名", "别名"), *infobox.edition_names(box))
    return list(dict.fromkeys(t.strip() for t in found if t.strip()))


def date_of(s: Subject, kids: Iterable[Subject]) -> str | None:
    if s.date:
        return s.date
    return min((k.date for k in kids if k.date), default=None)


def year_of(texts: Iterable[str]) -> int | None:
    for text in texts:
        for match in _YEAR.finditer(text):
            year = int(match.group())
            if YEAR_MIN <= year <= YEAR_MAX:
                return year
    return None


def status_of(t: Tally, box: Infobox, vocab: Vocab, min_votes: int) -> Status:
    """Official tag, else an end date in the infobox, else the best-voted user tag."""
    for key in t.official:
        status = vocab.status(key)
        if status is not None:
            return status
    if infobox.values(box, "结束", "连载结束"):
        return Status.COMPLETED
    voted = [
        (votes, status)
        for key, votes in t.user.items()
        if votes >= min_votes and (status := vocab.status(key)) is not None
    ]
    best = max(voted, key=lambda vs: vs[0], default=None)  # the first of equal votes
    return best[1] if best else Status.UNKNOWN


def vols_of(kind: Kind, kids: list[Subject], box: Infobox) -> int:
    if kind is Kind.VOLUME:
        return 1
    stated = [int(m[1] or m[2]) for v in infobox.values(box, "册数") if (m := _VOLS.search(v))]
    return max([len(kids), *stated])
