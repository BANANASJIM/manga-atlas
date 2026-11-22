"""Match catalogue works to a tag source's entries by Japanese title."""

from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence

from manga_atlas.build.tagsources.common import Work
from manga_atlas.build.vocab import has_kana

NAME, ALIAS = 0, 1  # how a title names a work: an original title beats an alias


def title_key(title: str) -> str:
    """A title as compared: NFKC, casefolded, with underscores (as wiki title lists spell spaces)
    and runs of spaces as one space."""
    return " ".join(unicodedata.normalize("NFKC", title).replace("_", " ").casefold().split())


def match(
    works: Iterable[Work],
    titles: Iterable[str],
    *,
    suffixes: Sequence[str] = (),
    linked: Mapping[int, str] | None = None,
) -> dict[int, str]:
    """Work id -> the entry of `titles` that names it, spelled as there.

    `linked` works (known by id, as Wikidata links Bangumi subjects) keep their entries, and no
    other work takes those. Every other work tries its original title, then each alias with kana,
    each first with one of `suffixes` ("名 (漫画)"), then bare. Works sharing an original title
    get no title match, since nothing tells them apart, and their title stays theirs: no other
    work takes it by alias. Nor does an entry several works claim go to any of them, unless only
    one claims it by original title.
    """
    linked = dict(linked or {})
    works = list(works)
    shared = Counter(title_key(w.name) for w in works)
    wanted: dict[int, list[tuple[str, int]]] = {}  # work -> (entry key, how), best first
    blocking: dict[int, list[tuple[str, int]]] = {}  # claims that only keep others out
    for w in works:
        own = title_key(w.name)
        if not own:
            continue
        names = [(title_key(w.name + s), NAME) for s in (*suffixes, "")]
        if shared[own] > 1:
            blocking[w.id] = names
        elif w.id not in linked:
            aliases = [a for a in w.aliases if has_kana(a) and title_key(a) != own]
            by_alias = [(title_key(a + s), ALIAS) for a in aliases for s in (*suffixes, "")]
            wanted[w.id] = names + by_alias

    asked = {k for keys in (*wanted.values(), *blocking.values()) for k, _ in keys}
    found: dict[str, str] = {}
    for title in titles:
        k = title_key(title)
        if k in asked:
            found.setdefault(k, title)

    taken = {title_key(t) for t in linked.values()}
    claims: dict[str, dict[int, int]] = defaultdict(dict)  # entry key -> work -> how
    for wid, keys in (*wanted.items(), *blocking.items()):
        for k, how in keys:
            if k in found and k not in taken:
                claims[k][wid] = min(how, claims[k].get(wid, how))
    out = dict(linked)
    for wid, keys in wanted.items():
        for k, _ in keys:
            claim = claims.get(k)
            if claim and [w for w, how in claim.items() if how == min(claim.values())] == [wid]:
                out[wid] = found[k]
                break
    return out


def settle(reached: Mapping[int, tuple[str, str]], linked: Collection[int]) -> dict[int, str]:
    """Work id -> the entry it keeps, given each work's matched title and the entry that title
    finally leads to (a wiki redirect followed). A linked work keeps its entry, and no other work
    takes a linked one. Of the other works reaching one entry, the one whose title is the entry
    itself keeps it if it is the only such work; works that came through a redirect lose it."""
    out = {w: reached[w][1] for w in linked if w in reached}
    reserved = {title_key(entry) for entry in out.values()}
    reaching: dict[str, list[int]] = defaultdict(list)
    for wid, (_, entry) in reached.items():
        if wid not in linked and title_key(entry) not in reserved:
            reaching[title_key(entry)].append(wid)
    for wids in reaching.values():
        direct = [w for w in wids if title_key(reached[w][0]) == title_key(reached[w][1])]
        keeps = wids if len(wids) == 1 else direct if len(direct) == 1 else []
        out.update((w, reached[w][1]) for w in keeps)
    return out
