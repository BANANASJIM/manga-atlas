"""ゆりみる (yurimiru.com): a Japanese yuri database's own tags.

refresh(): when the cache is older than yurimiru_refresh_days, read robots.txt and keep to it,
read the sitemap, and fetch the page of each work that is new or changed since it was fetched (its
sitemap lastmod) or was read by another version of read_page, one request at a time and spaced,
redirects followed only within the site and robots.txt. Of a page only what tags need is kept:
title, medium, tag chips, the 恋愛百合 mark and the 百合成分表. A refresh that fails, stops at the
cap or leaves pages unread keeps what it read but stays stale, so the next build goes on from there.
pages(): at build time, match the manga entries to works by Japanese title and turn each one's
chips into tags by the [yurimiru] table in vocab.toml.
"""

from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from manga_atlas.build.schema import TagSource
from manga_atlas.build.tagsources import common, titles
from manga_atlas.build.tagsources.common import Log, Page, Work
from manga_atlas.build.vocab import Vocab
from manga_atlas.config import Settings

TAG = TagSource.YURIMIRU
# {"entries": {site id: {"url", "lastmod", "parser", "title", "medium", "tags", "romance",
#  "scales": {scale: percent}}}}: what was fetched, matched to works at build time.
CACHE = "pages.json"
PARSER = 2  # read_page's version: an entry read by another is fetched anew
NEVER = "1970-01-01T00:00:00+00:00"  # the stamp of a cache no refresh has completed
_SITEMAP = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_GONE = (404, 410)  # no such page, for good
# A work page, as its markup says it (2026-11). Tag chips link to the tag's list; the 恋愛百合
# mark and the 百合成分表 are read from the page's text.
_SCRIPT = re.compile(r"<script\b.*?</script>", re.S)
_CHIP_LINK = "/works?tags="
_CHIP = re.compile(r'<a\b[^>]*?\bhref="/works\?tags=([^"&]*)[^"]*"')
_ROMANCE = re.compile(r"♡\s*恋愛百合")
_CHART = "百合成分表"
_SCALES = re.compile(r"(?:[^\s%]+度\s*\d+\s*%\s*)+")
_SCALE = re.compile(r"([^\s%]+度)\s*(\d+)\s*%")
_TAG = re.compile(r"<[^>]+>")


class _Refused(Exception):
    """A redirect to where we may not go: off the site, or somewhere robots.txt forbids."""


class Rules:
    """vocab.toml [yurimiru]: which chips are tags and what they are called here, and when a work
    is 百合."""

    def __init__(self, table: Mapping[str, Any], settings: Settings) -> None:
        self.names: dict[str, str] = dict(table["tags"])
        self.yuri = str(table["yuri"])
        self.sweetness = str(table["sweetness"])
        self.volume = re.compile(table["volume"])
        self.min_sweetness = settings.yurimiru_yuri_sweetness

    def title(self, raw: str) -> str:
        return self.volume.sub("", raw).strip()

    def tags(self, entry: Mapping[str, Any]) -> tuple[str, ...]:
        names = [self.names[c] for c in entry["tags"] if c in self.names]
        if entry["romance"] or entry["scales"].get(self.sweetness, 0) >= self.min_sweetness:
            names.append(self.yuri)
        return tuple(dict.fromkeys(names))


def enabled(settings: Settings) -> bool:
    return settings.yurimiru_enabled


def refresh(settings: Settings, works: Sequence[Work], log: Log) -> None:
    cache = settings.tag_cache(TAG.key) / CACHE
    old = common.read(cache, log)
    entries: dict[str, dict[str, Any]] = dict(old["entries"]) if old else {}
    reread = any(e.get("parser") != PARSER for e in entries.values())  # a parser change
    if old is not None and not reread and common.fresh(old, settings.yurimiru_refresh_days):
        return
    stamp = str(old[common.FETCHED_AT]) if old else NEVER
    log(f"yurimiru: {'no cache' if old is None else 'cache too old or unfinished'}, fetching")
    fetched = read = unreadable = 0
    todo: list[str] = []
    try:
        # Redirects are followed by _request, which checks each one.
        with common.client(settings, follow_redirects=False) as client:
            pace = common.Pace(settings.yurimiru_interval_s)
            robots = _robots(client, settings, pace)
            pace.interval = max(pace.interval, robots.delay)
            listed = _sitemap(client, settings, robots, pace)
            entries = {i: e for i, e in entries.items() if i in listed}  # gone from the site
            due = [
                i
                for i in sorted(listed, key=lambda i: listed[i][1], reverse=True)
                if _due(entries.get(i), listed[i][1]) and robots.allows(listed[i][0])
            ]  # the most recently changed first
            todo = due[: settings.yurimiru_max_pages] if settings.yurimiru_max_pages else due
            log(f"yurimiru: {len(listed):,} works listed, {len(due):,} new or changed")
            for i in todo:
                url, lastmod = listed[i]
                fetched += 1
                try:
                    page = _get(client, settings, pace, url, robots)
                except _Refused as e:
                    log(f"yurimiru: {url} leads to {e}, not followed")
                    continue
                if page is None:  # gone since the sitemap was written
                    continue
                try:
                    facts = read_page(page)
                except ValueError as e:  # an old entry stays, and the page is tried again
                    unreadable += 1
                    log(f"yurimiru: {url} not read ({e})")
                else:
                    entries[i] = {"url": url, "lastmod": lastmod, "parser": PARSER, **facts}
                    read += 1
                if fetched % settings.yurimiru_save_every == 0:
                    _save(cache, entries, stamp)
                    log(f"yurimiru: {fetched:,}/{len(todo):,} pages")
            if unreadable and not read:
                raise ValueError("no work page could be read: has the site changed?")
    except Exception as e:  # a supplementary source never fails the Bangumi build
        log(f"yurimiru: fetch failed, {read:,} of {len(todo):,} read ({type(e).__name__}: {e})")
        if fetched:
            try:
                _save(cache, entries, stamp)  # still stale: the next build goes on from here
            except OSError as e2:
                log(f"yurimiru: could not keep what was fetched ({e2})")
        return
    complete = len(todo) == len(due) and not unreadable
    _save(cache, entries, None if complete else stamp)
    unread = f", {unreadable:,} to read again" if unreadable else ""
    log(f"yurimiru: {fetched:,} pages fetched{unread}; {len(entries):,} works known")


def _due(entry: Mapping[str, Any] | None, lastmod: str) -> bool:
    """Whether a work's page needs fetching: new, changed, or read by another parser."""
    return entry is None or entry.get("lastmod") != lastmod or entry.get("parser") != PARSER


def _save(path: Path, entries: dict[str, dict[str, Any]], stamp: str | None) -> None:
    """Write the cache: stamped now when complete, else with the old stamp, still stale."""
    data: dict[str, Any] = {"entries": entries}
    common.write(path, data if stamp is None else {common.FETCHED_AT: stamp, **data})


def _robots(client: httpx.Client, settings: Settings, pace: common.Pace) -> common.Robots:
    """robots.txt as it reads now. Only a page that is not there (404/410) lets everything be
    fetched; a refusal (401/403) lets nothing; anything else, a busy site included, ends the
    refresh."""
    response = _request(client, settings, pace, settings.yurimiru_robots_url, _on_site(settings))
    if response.status_code in _GONE:
        return common.Robots.everything(allowed=True)
    if response.status_code in (401, 403):
        return common.Robots.everything(allowed=False)
    response.raise_for_status()
    return common.Robots(response.text, settings.user_agent)


def _get(
    client: httpx.Client, settings: Settings, pace: common.Pace, url: str, robots: common.Robots
) -> str | None:
    """A page's text, or None when it is gone."""
    on_site = _on_site(settings)
    response = _request(client, settings, pace, url, lambda u: on_site(u) and robots.allows(u))
    if response.status_code in _GONE:
        return None
    response.raise_for_status()
    return response.text


def _on_site(settings: Settings) -> Callable[[str], bool]:
    site = urlsplit(settings.yurimiru_sitemap_url)
    return lambda url: (urlsplit(url).scheme, urlsplit(url).netloc) == (site.scheme, site.netloc)


def _request(
    client: httpx.Client,
    settings: Settings,
    pace: common.Pace,
    url: str,
    allowed: Callable[[str], bool],
) -> httpx.Response:
    """One GET, spaced from the last, following redirects (each spaced too) only to URLs that are
    `allowed`; while the site is busy, wait as it asks and try again."""
    tries = hops = 0
    while True:
        pace.wait()
        response = client.get(url)
        if response.status_code in common.BUSY and tries < settings.yurimiru_busy_retries:
            tries += 1
            time.sleep(common.retry_after(response, settings.yurimiru_busy_wait_s * tries))
            continue
        if not response.is_redirect:
            return response
        target = urljoin(url, response.headers["location"])
        if not allowed(target):
            raise _Refused(target)
        if hops >= settings.yurimiru_redirects:
            raise _Refused(f"more than {settings.yurimiru_redirects} redirects")
        url, hops = target, hops + 1


def _sitemap(
    client: httpx.Client, settings: Settings, robots: common.Robots, pace: common.Pace
) -> dict[str, tuple[str, str]]:
    """Site id -> (page URL, lastmod) of every work the sitemap lists on the site itself."""
    if not robots.allows(settings.yurimiru_sitemap_url):
        raise ValueError("robots.txt does not let us read the sitemap")
    text = _get(client, settings, pace, settings.yurimiru_sitemap_url, robots)
    work, on_site = re.compile(settings.yurimiru_work_url), _on_site(settings)
    listed: dict[str, tuple[str, str]] = {}
    for url in ET.fromstring(text or "<urlset/>").iter(f"{_SITEMAP}url"):
        loc = (url.findtext(f"{_SITEMAP}loc") or "").strip()
        if on_site(loc) and (m := work.search(loc)):
            listed[m[1]] = (loc, (url.findtext(f"{_SITEMAP}lastmod") or "").strip())
    if not listed:  # a sitemap of another shape must not empty the cache
        raise ValueError("the sitemap lists no work pages")
    return listed


def read_page(text: str) -> dict[str, Any]:
    """What a work page says about the work's tags. ValueError when it is not a work page, or when
    a part of it that is there cannot be read (the site changed its markup)."""
    page = _SCRIPT.sub("", text)  # the scripts repeat the page as data
    title, medium = _cell(page, "作品名"), _cell(page, "ジャンル")
    if not title or not medium:
        raise ValueError("not a work page")
    chips = [unquote(q) for q in _CHIP.findall(page)]
    if len(chips) != page.count(_CHIP_LINK):
        raise ValueError("tag chips not read")
    words = " ".join(html.unescape(_TAG.sub(" ", page)).split())  # the page's text
    scales: dict[str, int] = {}
    if _CHART in words:
        run = _SCALES.search(words.split(_CHART, 1)[1])
        scales = {name: int(v) for name, v in _SCALE.findall(run[0])} if run else {}
        if not scales:
            raise ValueError(f"{_CHART} not read")
    return {
        "title": title,
        "medium": medium,
        "tags": list(dict.fromkeys(c.strip() for c in chips if c.strip())),
        "romance": _ROMANCE.search(words) is not None,
        "scales": scales,
    }


def _cell(page: str, label: str) -> str:
    """The value of a row of the work's information table."""
    m = re.search(rf">\s*{label}\s*</td>\s*<td[^>]*>(.*?)</td>", page, re.S)
    return _text(m[1]) if m else ""


def _text(markup: str) -> str:
    return html.unescape(_TAG.sub("", markup)).strip()


def pages(settings: Settings, works: Sequence[Work], vocab: Vocab, log: Log) -> dict[int, Page]:
    """Each work's ゆりみる manga entry, matched by Japanese title now, so new works and new rules
    take effect without a refetch. Entries sharing a title are left out: nothing tells them
    apart."""
    data = common.read(settings.tag_cache(TAG.key) / CACHE, log)
    if data is None:
        return {}
    try:
        rules = Rules(vocab.source_rules[TAG.key], settings)
        entries: dict[str, dict[str, Any]] = data["entries"]
        by_title: dict[str, list[str]] = defaultdict(list)
        for i, e in entries.items():
            if e["medium"] in settings.yurimiru_media:
                by_title[titles.title_key(rules.title(e["title"]))].append(i)
        lone = {k: ids[0] for k, ids in by_title.items() if len(ids) == 1}
        matched = titles.match(works, (rules.title(entries[i]["title"]) for i in lone.values()))
        out = {}
        for wid, title in matched.items():
            e = entries[lone[titles.title_key(title)]]
            out[wid] = Page(title, e["url"], rules.tags(e))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        log(f"yurimiru: cache unusable, no tags from it ({type(e).__name__}: {e})")
        return {}
    return out


SOURCE = common.Source(TAG, enabled, refresh, pages)
