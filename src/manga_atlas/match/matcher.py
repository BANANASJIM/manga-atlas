"""Matcher: decide whether a source's search result is the same work as a Bangumi entry.

Every rule exists because a measured failure needed it (tests/fixtures/matching holds the cases):
exact keys after folding, the 中文名+原名 concatenation, and a guarded fuzzy ratio.
Plus two fixes from the frozen re-test: 'A (B)' gives two titles, and a Chinese + English alias
also counts by its Chinese half. And one only an author can vouch for, worth ≈ at most: a title in
another translation (retitled), for works Bangumi gives no Chinese title.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from rapidfuzz import fuzz

from manga_atlas.config import Settings
from manga_atlas.match.normalize import chinese_part, compact, fold
from manga_atlas.text import simplified

_OPEN = r"\(（\[【〔「『<《"
_CLOSE = r"\)）\]】〕」』>》"
# A short trailing bracket: '终将成为你（彩色条漫）', '海盜戰記 (冰海戰記)'.
_TAIL_BRACKET = re.compile(rf"[{_OPEN}]([^{_CLOSE}]{{1,8}})[{_CLOSE}]\s*$")
_PART_ONLY = re.compile(r"\d+|[ivx]+")  # '(2)', '(II)': a part number, never decoration
_PART1_MARK = re.compile(r"(?<=[一-鿿])(?:i|1)$")  # 海虎Ⅰ is 海虎 (part one)
_PART_ARABIC = re.compile(r"(\d+)$")
_PART_ROMAN = re.compile(r"(?<=[一-鿿])(viii|vii|iii|vi|iv|ix|ii|i|v|x)$")
_PART_ORDINAL = re.compile(r"(\d+)(?:st|nd|rd|th)$")  # MAJOR 2nd
_PART_CN = re.compile(r"第([一二三四五六七八九十\d]+)[部季卷篇章]")  # 第五部
_CN_DIGITS = {c: i for i, c in enumerate("一二三四五六七八九", start=1)}
_ROMAN = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
    "x": 10,
}
_KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fa]")
_HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_AUTHOR_SPLIT = re.compile(r"[,，、/／&＆;；·・()（）\[\]【】]+")
_QUERY_TRIM = " -_·・~～:："

MIN_KEY = 2  # a one-character title or author token is never evidence on its own
MIN_LATIN = 3  # 'VINLAND SAGA' qualifies as an English half; 'II' does not


@dataclass(frozen=True)
class WorkNames:
    """A Bangumi work as the matcher sees it. Strings are raw; the matcher folds them."""

    work_id: int
    name: str  # original title
    name_cn: str  # Chinese title, may be empty
    aliases: tuple[str, ...] = ()  # other titles: infobox aliases, edition names
    authors: tuple[str, ...] = ()  # creator names in any script
    # (title, source id): titles Chinese sources list it under, vouched for by its author
    learned: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WorkKeys:
    keys: frozenset[str]
    concat: frozenset[str]
    authors: frozenset[str]


@dataclass(frozen=True)
class TitleMatch:
    rule: str  # "exact", "concat", "fuzzy" or "retitled"
    key: str  # the source title key that matched


def part_no(key: str) -> int:
    """Trailing part number (Arabic, or Roman after a Han character); absent means part one."""
    if m := _PART_ARABIC.search(key):
        return int(m.group(1))
    if m := _PART_ROMAN.search(key):
        return _ROMAN[m.group(1)]
    return 1


def part_mark(key: str) -> int:
    """part_no, also reading ordinals (2nd) and Chinese part numbers (第五部): how a sequel or
    another instalment says so in a title that shares nothing else with the work's."""
    if m := _PART_CN.search(key):
        n = m.group(1)
        if n.isdigit():
            return int(n)
        tens, _, ones = n.rpartition("十")
        return (_CN_DIGITS.get(tens, 1) * 10 if "十" in n else 0) + _CN_DIGITS.get(ones, 0)
    if m := _PART_ORDINAL.search(key):
        return int(m.group(1))
    return part_no(key)


def shares_run(a: str, b: str) -> bool:
    """Two keys share a run of two characters beyond ASCII, or of four ASCII ones: the series name
    a sequel keeps. Two Latin letters alone recur by chance ('li' in library and colorful)."""
    if any(not a[i : i + 2].isascii() and a[i : i + 2] in b for i in range(len(a) - 1)):
        return True
    return any(a[i : i + 4].isascii() and a[i : i + 4] in b for i in range(len(a) - 3))


def _chinese(title: str) -> bool:
    """Han characters and no kana: a Chinese title (or a Japanese one written in kanji only)."""
    return bool(_HAN.search(title)) and not _KANA.search(title)


def author_tokens(text: str) -> set[str]:
    """Comparable author tokens: each listed name whole, and its space-separated parts."""
    tokens: set[str] = set()
    for segment in _AUTHOR_SPLIT.split(unicodedata.normalize("NFKC", text)):
        for piece in (segment, *segment.split()):
            token = compact(piece)
            if len(token) >= MIN_KEY:
                tokens.add(token)
    return tokens


class Matcher:
    def __init__(self, settings: Settings) -> None:
        self.queries_max = settings.match_queries_max
        self.threshold = settings.match_fuzzy_threshold * 100  # rapidfuzz scores run 0-100
        self.short_len = settings.match_short_title_len
        editions = sorted({compact(w) for w in settings.match_edition_words} - {""}, key=len)
        self._edition = (
            re.compile("|".join(map(re.escape, reversed(editions)))) if editions else None
        )
        self._related = tuple(sorted({compact(w) for w in settings.match_related_words} - {""}))

    # ---- keys

    def _related_in(self, key: str) -> frozenset[str]:
        return frozenset(w for w in self._related if w in key)

    def _finish(self, key: str) -> str:
        if self._edition:
            key = self._edition.sub("", key)
        return _PART1_MARK.sub("", key) if len(key) > MIN_KEY else key

    def split(self, raw: str) -> list[str]:
        """'A (B)' -> [A, B] when the bracket is decoration or another name; else [raw].

        A bracket that names a side story, art book or part keeps the title whole, so
        '航海王（番外篇）' never becomes 航海王.
        """
        m = _TAIL_BRACKET.search(raw)
        if not m or not raw[: m.start()].strip():
            return [raw]
        inner = compact(m.group(1))
        if self._related_in(inner) or _PART_ONLY.fullmatch(inner):
            return [raw]
        return [raw[: m.start()], m.group(1)]

    def _keys(self, raw: str) -> tuple[str, str]:
        """(main key, key of the bracketed alternative name or "")."""
        parts = self.split(raw)
        main = self._finish(compact(parts[0]))
        alt = self._finish(compact(parts[1])) if len(parts) > 1 else ""
        return main, (alt if len(alt) >= MIN_KEY and not alt.isdigit() else "")

    def key(self, raw: str) -> str:
        """The main matching key of a title."""
        return self._keys(raw)[0]

    def title_keys(self, raw: str) -> list[str]:
        """All keys a title stands for: its main key, and the bracketed alternative name."""
        return [k for k in dict.fromkeys(self._keys(raw)) if k]

    def work_keys(self, names: WorkNames) -> WorkKeys:
        primaries = {t: self._keys(t) for t in (names.name, names.name_cn) if t}
        primary_keys = {k for pair in primaries.values() for k in pair if k}
        keys = set(primary_keys)
        for alias in names.aliases:  # never the learned titles: they are searched, not trusted
            if alias in primaries:
                continue
            keys.update(k for k in self._keys(alias) if k)
            han = chinese_part(alias, min_han=MIN_KEY, min_latin=MIN_LATIN)
            if han is None:
                continue
            k = self.key(han)
            # '进击的巨人 Before the fall' must not yield the main series' title.
            if len(k) >= MIN_KEY and not any(k != p and k in p for p in primary_keys):
                keys.add(k)
        concat: frozenset[str] = frozenset()
        if names.name and names.name_cn:
            kn, kc = primaries[names.name][0], primaries[names.name_cn][0]
            if kn and kc:
                concat = frozenset({kn + kc, kc + kn})
        authors = set().union(*(author_tokens(a) for a in names.authors))
        return WorkKeys(frozenset(keys), concat, frozenset(authors))

    # ---- decisions

    def match_title(self, title: str, work: WorkKeys) -> TitleMatch | None:
        """Exact key, then 中文名+原名 concatenation, then a guarded fuzzy ratio. No containment:
        it accepts sequels and side stories wholesale."""
        tkeys = self.title_keys(title)
        for t in tkeys:
            if t in work.keys:
                return TitleMatch("exact", t)
        for t in tkeys:
            if t in work.concat:
                return TitleMatch("concat", t)
        for t in tkeys:
            if len(t) <= self.short_len:
                continue
            for k in work.keys:
                if (
                    len(k) > self.short_len
                    and part_no(t) == part_no(k)
                    and self._related_in(t) == self._related_in(k)
                    and fuzz.ratio(t, k) >= self.threshold
                ):
                    return TitleMatch("fuzzy", t)
        return None

    def chinese_titled(self, names: WorkNames) -> bool:
        """The work has a Chinese title: Bangumi's, its own (a Chinese work, or a Japanese title in
        kanji only), or an alias (Wikidata's labels among them)."""
        return any(_chinese(t) for t in (names.name_cn, names.name, *names.aliases) if t)

    def retitled(self, title: str, work: WorkKeys, main: str) -> TitleMatch | None:
        """A title in another translation, for an author to vouch for: no run in common with any
        known title, and the same part number and side-story words as the work's own title `main`,
        so neither a sequel nor the main series of a side story passes for the work."""
        own = self.key(main)
        for t in self.title_keys(title):
            if (
                len(t) >= MIN_KEY
                and part_mark(t) == part_mark(own)
                and self._related_in(t) == self._related_in(own)
                and not any(shares_run(t, k) for k in work.keys)
            ):
                return TitleMatch("retitled", t)
        return None

    def author_match(self, author: str | None, work: WorkKeys) -> bool | None:
        """True / False when both sides name authors; None when either side is silent.
        Authors confirm a title match; they never veto one."""
        if not author or not work.authors:
            return None
        mine = author_tokens(author)
        if not mine:
            return None
        return any(
            a == b or (min(len(a), len(b)) > MIN_KEY and fuzz.ratio(a, b) >= self.threshold)
            for a in mine
            for b in work.authors
        )

    def is_short(self, match: TitleMatch) -> bool:
        """Short titles collide across works, so a title alone only earns ≈."""
        return len(match.key) <= self.short_len

    def other_work(self, match: TitleMatch, author: bool | None) -> bool:
        """同名不同作: a short title whose known author is someone else."""
        return author is False and self.is_short(match)

    # ---- queries

    def queries(
        self, names: WorkNames, lang: str, *, thorough: bool = False, source: str = ""
    ) -> list[str]:
        """Search strings for a source, tried in turn until one finds the work, without edition
        words, one per matching key, at most match_queries_max. A Japanese source: the original.
        A Chinese source: the work's Chinese title (Bangumi's, or its own if Chinese), alone unless
        `thorough` (the work page); with none, or thorough, then the titles the other Chinese
        sources (not `source`) list it under, the Chinese aliases (Chinese in Simplified, which all
        of them answer), the original, the other aliases."""
        if lang.startswith("ja"):
            return [self.strip_editions(q)] if (q := names.name or names.name_cn) else []
        own = names.name_cn or (names.name if _chinese(names.name) else "")
        if own and not thorough:
            return [self.strip_editions(simplified(own))]
        learned = [t for t, src in names.learned if src != source]
        chinese = [simplified(a) for a in (own, *learned, *names.aliases) if a and _chinese(a)]
        return self.distinct((*chinese, names.name, *names.aliases))

    def distinct(self, titles: Iterable[str]) -> list[str]:
        """Titles without edition words, one per matching key, at most match_queries_max."""
        found: dict[str, str] = {}
        for q in titles:
            if (q := self.strip_editions(q)).strip():
                found.setdefault(self.key(q), q)
        return list(found.values())[: self.queries_max]

    def strip_editions(self, title: str) -> str:
        if not self._edition:
            return title
        raw = unicodedata.normalize("NFKC", title)
        folded = fold(raw)
        spans = [m.span() for m in self._edition.finditer(folded)]
        if not spans:
            return title
        if len(folded) == len(raw):  # remove the same spans from the original script
            out, last = [], 0
            for start, end in spans:
                out.append(raw[last:start])
                last = end
            out.append(raw[last:])
            stripped = "".join(out)
        else:
            stripped = self._edition.sub("", folded)
        stripped = stripped.strip(_QUERY_TRIM).strip()
        return stripped or title
