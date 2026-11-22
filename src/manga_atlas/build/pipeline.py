"""Archive zip -> atlas.db, swapped into place atomically."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from manga_atlas.build import (
    archive,
    catalog,
    infobox,
    people,
    region,
    tags,
    tagsources,
    writer,
    zhnames,
)
from manga_atlas.build import vocab as vocab_rules
from manga_atlas.build.catalog import Selection
from manga_atlas.build.infobox import Infobox
from manga_atlas.build.people import Link, Person
from manga_atlas.build.schema import CREATOR_ROLES, ROLE_BY_INFOBOX, SCHEMA_VERSION
from manga_atlas.config import Settings
from manga_atlas.text import key

UNRANKED = 1 << 30  # rank_key of works without a rank: after every ranked one
MEMBERS = (archive.SUBJECTS, archive.RELATIONS, archive.CREDITS, archive.PERSONS)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass
class _Clock:
    start: float

    def lap(self, what: str) -> None:
        log(f"[{time.monotonic() - self.start:5.1f}s] {what}")


def build(settings: Settings, dump: Path) -> dict[str, Any]:
    """Rebuild atlas.db from an Archive zip. Returns summary numbers."""
    clock = _Clock(time.monotonic())
    vocab = vocab_rules.load()
    with zipfile.ZipFile(dump) as zf:
        missing = set(MEMBERS) - set(zf.namelist())
        if missing:
            raise ValueError(f"{dump.name} lacks {sorted(missing)}")
        comics, others = catalog.read_subjects(
            archive.rows(zf, archive.SUBJECTS), settings.standalone_min_collects
        )
        clock.lap(f"read {len(comics):,} manga subjects")
        parent, edges = catalog.read_relations(archive.rows(zf, archive.RELATIONS), comics)
        tagger = catalog.Tagger.for_subjects(comics.values(), vocab, settings)
        sel = catalog.select(comics, parent, tagger, settings)
        clock.lap(f"selected {len(sel.works):,} works")
        links = people.read_links(archive.rows(zf, archive.CREDITS), sel.works)
        persons = people.read_persons(archive.rows(zf, archive.PERSONS), {p for p, _, _ in links})
        clock.lap(f"read {len(persons):,} people")
    links = {link for link in links if link[0] in persons}
    boxes = {w: infobox.parse(s.infobox) for w, s in sel.works.items()}
    standins = people.add_infobox_credits(boxes, links, persons, vocab.key)
    relations, targets = catalog.relations(edges, sel, comics, others, tagger)
    del comics, others, edges

    person_works: dict[int, set[int]] = defaultdict(set)
    for pid, work, _ in links:
        person_works[pid].add(work)
    names: dict[str, set[int]] = defaultdict(set)
    for p in persons.values():
        for name in p.names():
            names[vocab.key(name)].add(p.id)
    pages = tagsources.collect(settings, _source_works(sel, boxes), vocab, log)
    zhnames.refresh(settings, log)
    if pages:
        clock.lap("read " + ", ".join(f"{len(v):,} {src.key} pages" for src, v in pages.items()))
    sourced = {src: {w: p.tags for w, p in found.items()} for src, found in pages.items()}
    voc = tags.build(sel.works, sel.tallies, tagger, names, person_works, settings, sourced)
    regions = region.assign(
        sel.tallies, links, {w: s.name for w, s in sel.works.items()}, vocab, settings
    )
    clock.lap(f"derived {len(voc.tags)} tags, {standins:,} unlinked credits")

    rows = writer.Rows(
        meta={
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "dump_id": archive.dump_id(dump),
            "works": str(len(sel.works)),
            "schema": str(SCHEMA_VERSION),
        }
    )
    credited = _people(rows, boxes, links, persons, person_works)
    _works(rows, sel, boxes, regions, credited, settings)
    rows.tags = [(t.id, t.key, t.name, t.group) for t in voc.tags]
    rows.tag_aliases = sorted(voc.aliases.items())
    rows.work_tags = voc.work_tags
    rows.work_pages = sorted(
        (w, int(src), p.title, p.url)
        for src, found in pages.items()
        for w, p in found.items()
        if w in sel.works
    )
    rows.relations = sorted(relations)
    rows.targets = sorted((i, *t) for i, t in targets.items())

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # A staging file of our own, so no other build can replace it while we write it.
    handle, name = tempfile.mkstemp(
        prefix=f"{settings.atlas_db.name}.", suffix=".new", dir=settings.data_dir
    )
    os.close(handle)
    fresh = Path(name)
    try:
        writer.write(fresh, rows)
        os.replace(fresh, settings.atlas_db)
    except BaseException:
        fresh.unlink(missing_ok=True)
        raise
    clock.lap(f"wrote {settings.atlas_db}")

    inferred = Counter(inf for r, inf in regions.values() if r)
    return {
        "dump_id": rows.meta["dump_id"],
        "works": len(sel.works),
        **{k: v for k, v in sel.stats.items() if k != "works"},
        "tags": len(voc.tags),
        "tags_dropped_as_names": [k for k, _ in voc.dropped_as_names[:10]],
        **{f"{src.key}_works": len(found) for src, found in pages.items()},
        **{f"{src.key}_tags": sum(t.group == src.key for t in voc.tags) for src in pages},
        "people": len(rows.persons),
        "unlinked_credits": standins,
        "region_tagged": inferred[False],
        "region_inferred": inferred[True],
        "db_mb": round(settings.atlas_db.stat().st_size / 1e6, 1),
        "seconds": round(time.monotonic() - clock.start, 1),
    }


def _source_works(sel: Selection, boxes: dict[int, Infobox]) -> list[tagsources.Work]:
    return [
        tagsources.Work(w, s.name.strip(), tuple(catalog.titles(s, boxes[w])), s.collects)
        for w, s in sel.works.items()
    ]


def _fold(texts: Iterable[str]) -> str:
    """Normalized, de-duplicated lines for the trigram index."""
    return "\n".join(dict.fromkeys(n for t in texts if (n := key(t))))


def _source_titles(settings: Settings) -> dict[int, list[str]]:
    """Titles Chinese sources list works under, vouched for by the author (state.db's availability
    cache), so search finds a work by them too. Search only: they never become aliases, which
    matching and library mapping trust."""
    out: defaultdict[int, list[str]] = defaultdict(list)
    zh = [s.id for s in settings.enabled_sources if s.lang.startswith("zh")]
    if not settings.state_db.exists() or not zh:
        return out
    con = sqlite3.connect(f"file:{settings.state_db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT work_id, title FROM availability WHERE author_match = 1"
            " AND state IN ('yes', 'maybe') AND title IS NOT NULL"
            f" AND source_id IN ({','.join('?' * len(zh))})",
            zh,
        )
        for wid, title in rows:
            out[wid].append(title)
    except sqlite3.OperationalError:  # no availability table yet
        pass
    finally:
        con.close()
    return out


def _sibling(label: str, own: Iterable[str], settings: Settings) -> bool:
    """A label naming a side story or another part (a related word the work's own titles lack,
    as カムイ伝's 'KAMUI外傳'): as an alias it would make the matcher take that work for this."""
    k, mine = key(label), " ".join(key(t) for t in own)
    return any((w := key(word)) in k and w not in mine for word in settings.match_related_words)


def _works(
    rows: writer.Rows,
    sel: Selection,
    boxes: dict[int, Infobox],
    regions: dict[int, tuple[str | None, bool]],
    credited: dict[int, str],
    settings: Settings,
) -> None:
    vocab = vocab_rules.load()
    listed = _source_titles(settings)
    wikidata = zhnames.names(settings, log)
    for w in sorted(sel.works):
        s, box, kind, kids = sel.works[w], boxes[w], sel.kinds[w], sel.children.get(w, [])
        date = catalog.date_of(s, kids)
        year = catalog.year_of([date or "", *infobox.values(box, "开始", "连载开始", "发售日")])
        status = catalog.status_of(sel.tallies[w], box, vocab, settings.tag_min_votes)
        place, inferred = regions[w]
        name, name_cn = s.name.strip(), s.name_cn.strip()
        rows.works.append(
            (
                w, kind.value, name, name_cn, name_cn or name, date, year, status.value, place,
                int(inferred), s.score or None, s.votes, s.rank or None, s.rank or UNRANKED,
                s.collects, catalog.vols_of(kind, kids, box),
            )
        )  # fmt: skip
        if s.summary.strip():
            rows.texts.append((w, s.summary.strip()))
        own = catalog.titles(s, box)
        labels = [t for t in wikidata.get(w, ()) if not _sibling(t, own, settings)]
        titles = list(dict.fromkeys([*own, *labels]))
        rows.aliases.extend((w, t) for t in titles)
        rows.work_keys.append((w, _fold([*titles, *listed.get(w, ())]), credited[w]))


def _people(
    rows: writer.Rows,
    boxes: dict[int, Infobox],
    links: set[Link],
    persons: dict[int, Person],
    person_works: dict[int, set[int]],
) -> dict[int, str]:
    """Fill the person rows; return each work's creator names for the trigram index."""
    creators: dict[int, list[str]] = defaultdict(list)
    for pid, work, role in sorted(links):
        if role in CREATOR_ROLES:
            p = persons[pid]
            creators[work].extend((p.name, p.name_cn))
    rows.work_persons = sorted((pid, work, role.value) for pid, work, role in links)
    for pid in sorted(person_works):
        p = persons[pid]
        rows.persons.append((pid, p.name, p.name_cn, p.kind, len(person_works[pid])))
        aliases = dict.fromkeys(a.strip() for a in p.aliases)
        rows.person_aliases.extend((pid, a) for a in aliases if a and a not in (p.name, p.name_cn))
        rows.person_keys.append((pid, _fold(p.names())))
    return {
        w: _fold([*creators.get(w, ()), *infobox.values(box, *ROLE_BY_INFOBOX)])
        for w, box in boxes.items()
    }
