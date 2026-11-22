"""Japanese Wikipedia categories as tags.

refresh(): when the cache is older than jawiki_refresh_days, download the list of article titles,
ask Wikidata which articles belong to Bangumi subjects, match works to articles by Japanese title,
and fetch the matched articles' categories from the MediaWiki API, one request at a time, spaced,
with maxlag. Any failure keeps the old cache. pages(): at build time, decide which work keeps each
article (titles.settle); a kept manga article gives its work the tags its categories map to by the
[jawiki] rules in vocab.toml.
"""

from __future__ import annotations

import gzip
import re
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from manga_atlas.build.schema import TagSource
from manga_atlas.build.tagsources import common, titles
from manga_atlas.build.tagsources.common import Log, Page, Work
from manga_atlas.build.vocab import Vocab
from manga_atlas.config import Settings

TAG = TagSource.JAWIKI
# What was fetched, for the build to decide from, so a rule change needs no refetch:
# {"works": {work id: {"title": matched, "article": where it leads, "linked": by Wikidata}},
# "categories": {article: [category]}}.
CACHE = "pages.json"
TITLES = "titles.gz"
TITLES_HEADER = "page_title"
_CHUNK = 1 << 20
# Manga series (Q21198342) and manga (Q8274) items with a Bangumi subject ID (P5732), and the
# titles of their articles on the site.
_SPARQL = """SELECT ?bangumi ?title WHERE {{
  VALUES ?kind {{ wd:Q21198342 wd:Q8274 }}
  ?item wdt:P31 ?kind ; wdt:P5732 ?bangumi .
  ?article schema:about ?item ; schema:isPartOf <{site}> ; schema:name ?title .
}}"""


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern[str]
    name: str  # the tag; {} stands for the pattern's `term` group


class Rules:
    """vocab.toml [jawiki]: which articles count, and which categories become which tags."""

    def __init__(self, table: Mapping[str, Any]) -> None:
        self.manga = re.compile(table["manga"])
        self.reject = re.compile(table["reject"])
        self.drop = tuple(re.compile(p) for p in table["drop"])
        self.rules = tuple(Rule(re.compile(r["match"]), r["name"]) for r in table["rules"])

    def accepts(self, categories: Iterable[str]) -> bool:
        """A manga article, not a disambiguation page."""
        cats = list(categories)
        return any(self.manga.search(c) for c in cats) and not any(
            self.reject.search(c) for c in cats
        )

    def tag(self, category: str) -> str | None:
        if any(p.search(category) for p in self.drop):
            return None
        for rule in self.rules:
            if m := rule.pattern.search(category):
                term = m["term"].strip()
                return rule.name.format(term) if term else None
        return None

    def tags(self, categories: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(t for c in categories if (t := self.tag(c))))


def enabled(settings: Settings) -> bool:
    return settings.jawiki_enabled


def article_url(settings: Settings, title: str) -> str:
    return settings.jawiki_article_url.format(title=quote(title.replace(" ", "_")))


def refresh(settings: Settings, works: Sequence[Work], log: Log) -> None:
    cache = settings.tag_cache(TAG.key) / CACHE
    old = common.read(cache, log)
    if old is not None and common.fresh(old, settings.jawiki_refresh_days):
        return
    log(f"jawiki: {'no cache' if old is None else 'cache too old'}, fetching")
    try:
        with common.client(settings) as client:
            dump = _download(client, settings, cache.with_name(TITLES))
            linked = _linked(client, settings, {w.id for w in works})
            matched = titles.match(
                works, _titles(dump), suffixes=settings.jawiki_title_suffixes, linked=linked
            )
            chosen = _most_collected(works, matched, settings)
            cut = f", the {len(chosen):,} most collected kept" if len(chosen) < len(matched) else ""
            log(f"jawiki: {len(matched):,} works matched ({len(linked):,} by Wikidata){cut}")
            matched = {w: t.replace("_", " ") for w, t in chosen}
            leads, categories = _categories(client, settings, sorted(set(matched.values())), log)
        found = {
            str(w): {"title": t, "article": leads[t], "linked": w in linked}
            for w, t in sorted(matched.items())
            if t in leads
        }
        common.write(cache, {"works": found, "categories": categories})
    except Exception as e:  # a supplementary source never fails the Bangumi build
        log(f"jawiki: fetch failed, keeping the old cache ({type(e).__name__}: {e})")
        return
    log(f"jawiki: {len(found):,} works lead to an article ({len(categories):,} articles)")


def _most_collected(
    works: Sequence[Work], matched: dict[int, str], settings: Settings
) -> list[tuple[int, str]]:
    """The matched works, all of them or (in a trial run) only the most collected."""
    order = sorted((w for w in works if w.id in matched), key=lambda w: (-w.collects, w.id))
    if settings.jawiki_max_pages:
        order = order[: settings.jawiki_max_pages]
    return [(w.id, matched[w.id]) for w in order]


def _download(client: httpx.Client, settings: Settings, dest: Path) -> Path:
    part = dest.with_name(dest.name + ".part")
    with client.stream("GET", settings.jawiki_titles_url) as response:
        response.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with part.open("wb") as fh:
            for chunk in response.iter_bytes(_CHUNK):
                fh.write(chunk)
    part.replace(dest)
    return dest


def _titles(path: Path) -> Iterator[str]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            title = line.rstrip("\n")
            if title and title != TITLES_HEADER:
                yield title


def _linked(client: httpx.Client, settings: Settings, ids: set[int]) -> dict[int, str]:
    """Work id -> its article, by Wikidata's Bangumi links (the first title when several)."""
    found: dict[int, str] = {}
    for row in common.sparql(client, settings, _SPARQL.format(site=settings.jawiki_site)):
        value, title = row["bangumi"]["value"], row["title"]["value"]
        if value.isdigit() and int(value) in ids:
            found[int(value)] = min(title, found.get(int(value), title))
    return found


def _query(
    client: httpx.Client, settings: Settings, pace: common.Pace, params: dict[str, str]
) -> dict[str, Any]:
    """One API request, posted since 50 titles may not fit in a URL; while the replicas lag or the
    API is busy (429/503), wait as long as it asks and try again."""
    tries = 0
    while True:
        pace.wait()
        response = client.post(settings.jawiki_api_url, data=params)
        if response.status_code in common.BUSY and tries < settings.jawiki_lag_retries:
            tries += 1
            time.sleep(common.retry_after(response, settings.jawiki_busy_wait_s * tries))
            continue
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        error = data.get("error")
        if error is None:
            return data
        if error.get("code") != "maxlag" or tries >= settings.jawiki_lag_retries:
            raise ValueError(f"MediaWiki API: {error.get('code')}: {error.get('info')}")
        tries += 1
        time.sleep(common.retry_after(response, settings.jawiki_maxlag_s))


def _categories(
    client: httpx.Client, settings: Settings, asked: list[str], log: Log
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Asked title -> the article it leads to (through redirects); article -> its visible
    categories, without the namespace. Missing pages are left out."""
    pace = common.Pace(settings.jawiki_interval_s)
    pages: dict[str, str] = {}
    categories: dict[str, set[str]] = {}
    for start in range(0, len(asked), settings.jawiki_batch):
        batch = asked[start : start + settings.jawiki_batch]
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "categories",
            "clshow": "!hidden",
            "cllimit": "max",
            "redirects": "1",
            "maxlag": str(settings.jawiki_maxlag_s),
            "titles": "|".join(batch),
        }
        moved: dict[str, str] = {}
        more: dict[str, str] = {}
        while True:
            data = _query(client, settings, pace, params | more)
            query = data.get("query", {})
            for hop in (*query.get("normalized", ()), *query.get("redirects", ())):
                moved[hop["from"]] = hop["to"]
            for page in query.get("pages", ()):
                if "missing" in page or "invalid" in page:
                    continue
                cats = categories.setdefault(page["title"], set())
                cats.update(c["title"].split(":", 1)[-1] for c in page.get("categories", ()))
            if "continue" not in data:
                break
            more = data["continue"]
        for title in batch:
            page = _follow(title, moved)
            if page in categories:
                pages[title] = page
        done = start + len(batch)
        if done % (settings.jawiki_batch * 50) == 0:
            log(f"jawiki: categories of {done:,}/{len(asked):,} articles")
    return pages, {page: sorted(cats) for page, cats in categories.items()}


def _follow(title: str, moved: Mapping[str, str]) -> str:
    seen = set()
    while title in moved and title not in seen:
        seen.add(title)
        title = moved[title]
    return title


def pages(settings: Settings, works: Sequence[Work], vocab: Vocab, log: Log) -> dict[int, Page]:
    """Matched when fetched: the article titles to fetch came from matching."""
    data = common.read(settings.tag_cache(TAG.key) / CACHE, log)
    if data is None:
        return {}
    try:
        rules = Rules(vocab.source_rules[TAG.key])
        out = {}
        for wid, article in _owned(data).items():
            cats = data["categories"].get(article)
            if cats is not None and rules.accepts(cats):
                out[wid] = Page(article, article_url(settings, article), rules.tags(cats))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        log(f"jawiki: cache unusable, no tags from it ({type(e).__name__}: {e})")
        return {}
    return out


def _owned(data: dict[str, Any]) -> dict[int, str]:
    """Work id -> the article it keeps, decided from what was fetched."""
    works = data["works"]
    reached = {int(w): (str(e["title"]), str(e["article"])) for w, e in works.items()}
    return titles.settle(reached, {int(w) for w, e in works.items() if e["linked"]})


SOURCE = common.Source(TAG, enabled, refresh, pages)
