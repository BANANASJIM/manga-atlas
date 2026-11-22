"""Region of each work: from its tags, else inferred from its publishers or its title's script."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping

from manga_atlas.build.catalog import Tally
from manga_atlas.build.people import Link
from manga_atlas.build.schema import Role
from manga_atlas.build.vocab import Vocab
from manga_atlas.config import Settings

_PUBLISHING = (Role.PUBLISHER, Role.MAGAZINE)


def tagged(t: Tally, vocab: Vocab, min_votes: int) -> str | None:
    """An official region tag (the best-voted if several), else a user tag with enough votes."""
    official = [k for k in t.official if k in vocab.regions]
    if official:
        return vocab.regions[max(official, key=lambda k: t.user.get(k, 0))]
    voted = (k for k, votes in t.user.items() if k in vocab.regions and votes >= min_votes)
    best = max(voted, key=lambda k: t.user[k], default=None)  # the first of equal votes
    return vocab.regions[best] if best else None


def assign(
    tallies: Mapping[int, Tally],
    links: Iterable[Link],
    titles: Mapping[int, str],
    vocab: Vocab,
    settings: Settings,
) -> dict[int, tuple[str | None, bool]]:
    """work -> (region, inferred). A publisher or magazine predicts a region when enough of its
    tagged works agree; untagged works take their publishers' majority, else the script rule."""
    known = {w: r for w, t in tallies.items() if (r := tagged(t, vocab, settings.tag_min_votes))}
    publishers: dict[int, set[int]] = defaultdict(set)
    for pid, work, role in links:
        if role in _PUBLISHING and pid > 0:
            publishers[work].add(pid)
    counts: dict[int, Counter[str]] = defaultdict(Counter)
    for work, region in known.items():
        for pid in publishers.get(work, ()):
            counts[pid][region] += 1
    predicts: dict[int, str] = {}
    for pid, c in counts.items():
        total = sum(c.values())
        region, n = c.most_common(1)[0]
        share = n / total
        if total >= settings.region_entity_min_works and share >= settings.region_entity_min_share:
            predicts[pid] = region
    out: dict[int, tuple[str | None, bool]] = {}
    for work in tallies:
        if work in known:
            out[work] = (known[work], False)
            continue
        votes = Counter(predicts[p] for p in publishers.get(work, ()) if p in predicts)
        guess = votes.most_common(1)[0][0] if votes else vocab.script_region(titles[work])
        out[work] = (guess, guess is not None)
    return out
