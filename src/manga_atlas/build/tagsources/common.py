"""What every tag source shares: the works it matches, the pages it finds, its cache file."""

from __future__ import annotations

import contextlib
import email.utils
import json
import math
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from manga_atlas.build.schema import TagSource
from manga_atlas.build.vocab import Vocab
from manga_atlas.config import Settings

Log = Callable[[str], None]
FETCHED_AT = "fetched_at"  # every cache file says when it was fetched
BUSY = (429, 503)  # a site asking clients to slow down


@dataclass(frozen=True)
class Work:
    """A catalogue work as tag sources see it."""

    id: int
    name: str  # original title
    aliases: tuple[str, ...]  # its other titles, in any language
    collects: int  # popularity: a trial run fetches the most collected works first


@dataclass(frozen=True)
class Page:
    """A work's page at a source, and the tags it gives the work, spelled as the source does."""

    title: str
    url: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Source:
    tag: TagSource
    enabled: Callable[[Settings], bool]
    # Fetch anew when the cache is too old; a failure keeps the old cache and never raises.
    refresh: Callable[[Settings, Sequence[Work], Log], None]
    # Work id -> its page, from the cache (matched against the works here, or when fetched).
    pages: Callable[[Settings, Sequence[Work], Vocab, Log], dict[int, Page]]


def read(path: Path, log: Log) -> dict[str, Any] | None:
    """A cache file, or None when it is missing or unreadable."""
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        datetime.fromisoformat(data[FETCHED_AT])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as e:
        log(f"{path}: unreadable, ignored ({type(e).__name__}: {e})")
        return None
    return dict(data)


def fresh(data: dict[str, Any], days: int) -> bool:
    fetched = datetime.fromisoformat(data[FETCHED_AT])
    return datetime.now(UTC) - fetched < timedelta(days=days)


def client(settings: Settings, *, follow_redirects: bool = True) -> httpx.Client:
    """The HTTP client of a tag source: our User-Agent and the download timeout."""
    return httpx.Client(
        headers={"User-Agent": settings.user_agent},
        timeout=settings.download_timeout_s,
        follow_redirects=follow_redirects,
    )


def sparql(client: httpx.Client, settings: Settings, query: str) -> list[dict[str, Any]]:
    """The result rows of a query at the Wikidata SPARQL endpoint."""
    response = client.get(
        settings.wikidata_sparql_url,
        params={"query": query, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
    )
    response.raise_for_status()
    return list(response.json()["results"]["bindings"])


def write(path: Path, data: dict[str, Any]) -> None:
    """Replace the cache file at once, stamped with the time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    stamped = {FETCHED_AT: datetime.now(UTC).isoformat(timespec="seconds"), **data}
    part.write_text(json.dumps(stamped, ensure_ascii=False), encoding="utf-8")
    os.replace(part, path)


class Pace:
    """At least `interval` seconds between the starts of two requests."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.last = -math.inf

    def wait(self) -> None:
        delay = self.last + self.interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()


def retry_after(response: httpx.Response, default: float) -> float:
    """The wait a site asked for, as seconds or as an HTTP date; `default` when it said none."""
    value = response.headers.get("Retry-After")
    if value is None:
        return default
    with contextlib.suppress(ValueError):
        return max(0.0, float(value))
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return default
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


Rule = tuple[int, bool, re.Pattern[str]]  # length, allows, pattern


@dataclass
class _Group:
    agents: set[str] = field(default_factory=set)
    rules: list[Rule] = field(default_factory=list)
    delays: list[float] = field(default_factory=list)


class Robots:
    """robots.txt read as RFC 9309 says: the groups naming our product token, else those for "*";
    of their rules the longest matching a path decides, Allow winning a tie; no match allows."""

    def __init__(self, text: str, agent: str) -> None:
        token = agent.split("/", 1)[0].strip().casefold()
        groups: list[_Group] = []
        naming = False  # user-agent lines before the next rule name one group
        for raw in text.splitlines():
            key, _, value = raw.split("#", 1)[0].partition(":")
            key, value = key.strip().casefold(), value.strip()
            if key == "user-agent":
                if not naming:
                    groups.append(_Group())
                groups[-1].agents.add(value.casefold())
                naming = True
            elif key in ("allow", "disallow", "crawl-delay"):
                naming = False
                if groups and key == "crawl-delay":
                    with contextlib.suppress(ValueError):
                        groups[-1].delays.append(float(value))
                elif groups and value:
                    groups[-1].rules.append((len(value), key == "allow", _pattern(value)))
            # anything else (blank lines, comments, sitemaps) leaves the groups as they are
        ours = [g for g in groups if token in g.agents] or [g for g in groups if "*" in g.agents]
        self.rules = sorted((r for g in ours for r in g.rules), key=lambda r: (-r[0], not r[1]))
        self.delay = max((d for g in ours for d in g.delays), default=0.0)

    @classmethod
    def everything(cls, allowed: bool) -> Robots:
        return cls("" if allowed else "User-agent: *\nDisallow: /", "")

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return next((allows for _, allows, rule in self.rules if rule.match(path)), True)


def _pattern(path: str) -> re.Pattern[str]:
    """A robots.txt path: matched from the start; * stands for anything, a final $ for the end."""
    body = re.escape(path.removesuffix("$")).replace(r"\*", ".*")
    return re.compile(body + ("$" if path.endswith("$") else ""))
