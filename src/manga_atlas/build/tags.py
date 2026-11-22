"""Tag vocabulary: official tags plus widely used user tags, minus noise and people's names, plus
what the other tag sources say (merged into the same tag where the spellings fold together).

Every vote count is stored; the votes needed for a user tag to count are applied at query time.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from manga_atlas.build.catalog import Subject, Tagger, Tally
from manga_atlas.build.schema import TagSource
from manga_atlas.config import Settings
from manga_atlas.text import simplified


@dataclass(frozen=True)
class Tag:
    id: int
    key: str  # canonical normalized spelling
    name: str  # display spelling
    group: str


@dataclass
class Vocabulary:
    tags: list[Tag]
    work_tags: list[tuple[int, int, int, int, int]]  # tag, work, user votes, official, TagSource
    aliases: dict[str, int]  # every normalized spelling -> tag id
    dropped_as_names: list[tuple[str, int]] = field(default_factory=list)


def build(
    works: Mapping[int, Subject],
    tallies: Mapping[int, Tally],
    tagger: Tagger,
    names: Mapping[str, set[int]],
    person_works: Mapping[int, set[int]],
    settings: Settings,
    sourced: Mapping[TagSource, Mapping[int, Sequence[str]]] | None = None,
) -> Vocabulary:
    """`names`: normalized person name -> person ids; `person_works`: person id -> work ids;
    `sourced`: each other source's tags per work, spelled as the source does."""
    vocab = tagger.vocab
    marks: dict[int, dict[str, tuple[int, bool]]] = {}  # work -> tag -> (votes, official)
    for work, t in tallies.items():
        row = {k: (v, False) for k, v in t.user.items() if _is_tag(tagger, k)}
        for k in t.official:
            if _is_tag(tagger, k):
                row[k] = (row.get(k, (0, False))[0], True)
        marks[work] = row
    given, given_spelled, main_source = _sourced(tallies, tagger, sourced or {})

    official = {k for row in marks.values() for k, (_, off) in row.items() if off}
    strong: dict[str, set[int]] = defaultdict(set)
    counted: Counter[str] = Counter()
    for work, row in marks.items():
        for k, (votes, off) in row.items():
            if votes >= settings.tag_min_votes:
                strong[k].add(work)
            if off or votes >= settings.tag_min_votes or k in given.get(work, ()):
                counted[k] += 1
    for work, extra in given.items():
        counted.update(k for k in extra if k not in marks.get(work, ()))

    keys = set(official)
    dropped: list[tuple[str, int]] = []
    for k, ws in strong.items():
        if k in keys or len(ws) < settings.tag_vocab_min_works:
            continue
        by_name = set().union(*(person_works.get(p, set()) for p in names.get(k, ())))
        if len(ws & by_name) >= settings.tag_person_noise_share * len(ws):
            dropped.append((k, len(ws)))
            continue
        keys.add(k)
    bangumi = set(keys)
    keys.update(k for k in main_source if counted[k] >= settings.source_tag_min_works)

    official_spelled, user_spelled = _spellings(works, tagger)
    tags = []
    for i, k in enumerate(sorted(keys, key=lambda k: (-counted[k], k)), start=1):
        if k in bangumi:
            name, group = _display(k, tagger, official_spelled, user_spelled), vocab.group(k)
        else:  # spelled and grouped as the source has it, unless the vocabulary says otherwise
            name = vocab.display.get(k) or given_spelled[k].most_common(1)[0][0]
            group = vocab.groups.get(k, main_source[k].key)
        tags.append(Tag(i, k, name, group))
    ids = {t.key: t.id for t in tags}
    work_tags = []
    for work in marks.keys() | given.keys():
        row, extra = marks.get(work, {}), given.get(work, {})
        for k in row.keys() | extra.keys():
            if k in ids:
                votes, off = row.get(k, (0, False))
                src = (TagSource.BANGUMI if k in row else 0) | extra.get(k, 0)
                work_tags.append((ids[k], work, votes, int(off), int(src)))
    work_tags.sort()
    spelled = {*tagger.known, *(vocab.key(s) for c in given_spelled.values() for s in c)}
    aliases = {s: ids[k] for s in spelled if (k := tagger.canon(s)) in ids}
    aliases.update({v: ids[k] for v, k in vocab.aliases.items() if k in ids})
    aliases.update(ids)
    dropped.sort(key=lambda d: -d[1])
    return Vocabulary(tags, work_tags, aliases, dropped)


def _sourced(
    tallies: Mapping[int, Tally],
    tagger: Tagger,
    sourced: Mapping[TagSource, Mapping[int, Sequence[str]]],
) -> tuple[dict[int, dict[str, int]], dict[str, Counter[str]], dict[str, TagSource]]:
    """The other sources' tags folded like Bangumi's: work -> tag -> TagSource bits; each tag's
    spellings at the sources, by how many works use them; the source giving each tag to the most
    works (on a tie, the first)."""
    given: dict[int, dict[str, int]] = defaultdict(dict)
    spelled: dict[str, Counter[str]] = defaultdict(Counter)
    reach: dict[str, Counter[TagSource]] = defaultdict(Counter)
    for src in sorted(sourced):
        for work, raws in sourced[src].items():
            if work not in tallies:
                continue
            for raw in raws:
                k = tagger.canon(raw)
                if k and _is_tag(tagger, k):
                    if not given[work].get(k, 0) & src:
                        reach[k][src] += 1
                    given[work][k] = given[work].get(k, 0) | src
                    spelled[k][raw.strip()] += 1
    main = {k: max(c, key=lambda src: (c[src], -src)) for k, c in reach.items()}
    return given, spelled, main


def _is_tag(tagger: Tagger, key: str) -> bool:
    return not tagger.vocab.is_noise(key) and not tagger.vocab.is_attribute(key)


def _spellings(
    works: Mapping[int, Subject], tagger: Tagger
) -> tuple[dict[str, Counter[str]], dict[str, Counter[str]]]:
    """Canonical tag -> votes for each of its own spellings, official and user."""
    official: dict[str, Counter[str]] = defaultdict(Counter)
    user: dict[str, Counter[str]] = defaultdict(Counter)
    for s in works.values():
        for raw, count in s.tags:
            if tagger.vocab.key(raw) == (k := tagger.canon(raw)):
                user[k][raw.strip()] += count
        for raw in s.meta:
            if tagger.vocab.key(raw) == (k := tagger.canon(raw)):
                official[k][raw.strip()] += 1
    return official, user


def _display(key: str, tagger: Tagger, *spelled: Mapping[str, Counter[str]]) -> str:
    """Curated spelling, else the most used official spelling, else the most voted user one,
    in Simplified characters."""
    if key in tagger.vocab.display:
        return tagger.vocab.display[key]
    for votes in spelled:
        if key in votes:
            return simplified(votes[key].most_common(1)[0][0])
    return key
