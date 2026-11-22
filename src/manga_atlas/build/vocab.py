"""Tag rules: spelling folding, aliases, noise, groups. The curated data lives in vocab.toml."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Container
from functools import cache
from importlib import resources
from typing import Any

from manga_atlas.build.schema import EDITED, STATUS_LABELS, Status
from manga_atlas.text import key

DEFAULT_GROUP = "tag"
# Hiragana and katakana without the middle dot and long-vowel mark, which Chinese titles use too.
_KANA = re.compile(r"[ぁ-ゖァ-ヺ]")
_HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")


class Vocab:
    def __init__(self, data: dict[str, Any]) -> None:
        self._keys: dict[str, str] = {}
        self.noise = frozenset(self.key(x) for x in data["noise"])
        self.noise_patterns = tuple(re.compile(p) for p in data["noise_patterns"])
        self.suffixes = tuple(self.key(s) for s in data["strip_suffixes"])
        self.r18_official = frozenset(self.key(x) for x in data["r18_official"])
        self.regions: dict[str, str] = {self.key(r): str(r) for r in data["regions"]}
        self.statuses = {self.key(label): code for code, label in STATUS_LABELS.items()}
        self.script_regions: dict[str, str] = dict(data["script_regions"])
        self.display: dict[str, str] = {}  # canonical key -> curated spelling
        self.aliases: dict[str, str] = {}
        for variant, canonical in data["aliases"].items():
            self.aliases[self.key(variant)] = self.key(canonical)
            self.display.setdefault(self.key(canonical), canonical)
        self.groups: dict[str, str] = {}
        for group, names in data["groups"].items():
            for name in names:
                self.groups[self.key(name)] = group
                self.display.setdefault(self.key(name), name)
        # Each other tag source's own rules, in the table named by its key.
        self.source_rules: dict[str, dict[str, Any]] = {s.key: data.get(s.key, {}) for s in EDITED}

    def key(self, raw: str) -> str:
        """Normalized spelling: NFKC, Traditional to Simplified, casefold, single spaces."""
        hit = self._keys.get(raw)
        if hit is None:
            hit = self._keys[raw] = key(raw)
        return hit

    def canonical(self, key: str, known: Container[str]) -> str:
        """The tag a normalized spelling belongs to. `known` holds every spelling in the dump."""
        hit = self.aliases.get(key)
        if hit is not None:
            return hit
        for suffix in self.suffixes:
            base = key.removesuffix(suffix)
            if base and base != key and (base in known or base in self.aliases):
                return self.aliases.get(base, base)
        return key

    def is_noise(self, key: str) -> bool:
        return key in self.noise or any(p.search(key) for p in self.noise_patterns)

    def is_attribute(self, key: str) -> bool:
        """Region and status values become work columns, not tags."""
        return key in self.regions or key in self.statuses

    def status(self, key: str) -> Status | None:
        code = self.statuses.get(key)
        return None if code is None else Status(code)

    def group(self, key: str) -> str:
        return self.groups.get(key, DEFAULT_GROUP)

    def script_region(self, title: str) -> str | None:
        if has_kana(title):
            return self.script_regions["kana"]
        if _HANGUL.search(title):
            return self.script_regions["hangul"]
        return None


def has_kana(text: str) -> bool:
    """Whether the text is Japanese for certain: it has hiragana or katakana."""
    return _KANA.search(text) is not None


@cache
def load() -> Vocab:
    text = resources.files("manga_atlas.build").joinpath("vocab.toml").read_text("utf-8")
    return Vocab(tomllib.loads(text))
