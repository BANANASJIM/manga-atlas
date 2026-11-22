"""Shared helpers for the browser tests."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

from .fake_backend import Fake, Params


def query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def has_query(**expected: str | None) -> Callable[[str], bool]:
    """URL predicate: each key has exactly that value, or is absent when the value is None."""

    def check(url: str) -> bool:
        q = query(url)
        return all(q.get(k) == [v] if v is not None else k not in q for k, v in expected.items())

    return check


def total_text(fake: Fake, **params: str) -> str:
    """The results summary the page should show for these search parameters."""
    return f"共 {fake.search(Params.parse(params))['total']:,} 部"
