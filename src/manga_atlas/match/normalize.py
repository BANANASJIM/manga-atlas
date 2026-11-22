"""Text folding for matching: one comparable form for Simplified, Traditional and Japanese."""

from __future__ import annotations

import unicodedata
from importlib import resources

from manga_atlas.text import norm

# OpenCC's Japanese shinjitai -> traditional table (Apache-2.0), so 進撃 folds like 進擊.
_SHINJITAI_FILE = "data/JPShinjitaiCharacters.txt"
_PARTICLE = {"の": "的"}  # 進撃の巨人 ~ 进击的巨人
_VARIANTS = {"妳": "你", "姊": "姐", "牠": "它", "祂": "他", "衹": "只"}  # t2s leaves these


def _fold_table() -> dict[int, str]:
    text = resources.files(__package__).joinpath(_SHINJITAI_FILE).read_text(encoding="utf-8")
    table: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("#") or "\t" not in line:
            continue
        key, values = line.split("\t", 1)
        table[key] = values.split(" ")[0]
    return str.maketrans({**table, **_PARTICLE, **_VARIANTS})


_FOLD = _fold_table()


def fold(text: str) -> str:
    """NFKC, Japanese forms and stray variants folded, then the shared norm() (Simplified,
    casefold)."""
    return norm(unicodedata.normalize("NFKC", text).translate(_FOLD))


def compact(text: str) -> str:
    """fold() keeping only letters and digits: the raw material of every matching key."""
    return "".join(ch for ch in fold(text) if unicodedata.category(ch)[0] in "LN")


def _script(ch: str) -> str:
    """H (Han), L (ASCII letter), X (any other letter or digit), or "" (punctuation, space)."""
    if "㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿":
        return "H"
    if ch.isascii() and ch.isalpha():
        return "L"
    return "X" if unicodedata.category(ch)[0] in "LN" else ""


def chinese_part(text: str, *, min_han: int, min_latin: int) -> str | None:
    """The Han half of a Chinese + English title ('海盜戰記VINLAND SAGA' -> '海盜戰記').

    Only for exactly one Han run next to one Latin run; digits, kana and other scripts mean the
    title is not a simple pair (and a digit may be a part number we must keep).
    """
    s = unicodedata.normalize("NFKC", text)
    runs: list[tuple[str, int, int]] = []  # (script, first index, last index)
    for i, ch in enumerate(s):
        kind = _script(ch)
        if not kind:
            continue
        if kind == "X":
            return None
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1], i)
        else:
            runs.append((kind, i, i))
    if [r[0] for r in runs] not in (["H", "L"], ["L", "H"]):
        return None
    han = next(r for r in runs if r[0] == "H")
    latin = next(r for r in runs if r[0] == "L")
    han_text = s[han[1] : han[2] + 1]
    latin_text = "".join(c for c in s[latin[1] : latin[2] + 1] if c.isalpha()).casefold()
    if sum(_script(c) == "H" for c in han_text) < min_han or len(latin_text) < min_latin:
        return None
    if set(latin_text) <= set("ivx"):  # '海虎 III' is part three, not an English title
        return None
    return han_text
