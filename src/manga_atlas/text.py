"""Text normalization shared by the catalogue build, queries and matching."""

from __future__ import annotations

import unicodedata

from opencc import OpenCC

_t2s = OpenCC("t2s")


def simplified(text: str) -> str:
    """NFKC, then Traditional to Simplified; the case is kept, for a spelling to show."""
    return str(_t2s.convert(unicodedata.normalize("NFKC", text)))


def norm(text: str) -> str:
    """NFKC, then Traditional to Simplified, then casefold."""
    return simplified(text).casefold()


def key(text: str) -> str:
    """`norm` with single spaces: a tag or name spelling as the catalogue keys it."""
    return " ".join(norm(text).split())
