"""Where can a work be read? Search each source once, match the first results, cache verdicts.

Verdicts: yes (✓) when an author confirms the match or the title is long enough to trust;
maybe (≈) for a short title whose author is unknown; no, also for a short title whose author is
known to differ (同名不同作: the row keeps that entry's title and author_match false); down (not
cached). On long titles authors confirm, never veto.

Work page: all sources in parallel; a matching candidate without an author gets a details call
so an author can confirm it and dead entries drop out. "检查本页": searches only, sources in
order (Chinese first), stopping at the first ✓ or ≈ per work.

A work Bangumi gives no Chinese title: a Chinese source is searched by its other names in turn
(Matcher.queries), and when no title matches, its top result can be ≈ under another translation if
its author vouches and no other listed result is by that author (Matcher.retitled). Such titles
are learned off the cache (cache.listed): other Chinese sources search them, a cached miss
older than one is asked again, and the work page re-asks its misses once with a newly learned one.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Literal

from manga_atlas.availability import cache, state
from manga_atlas.availability.executor import BreakerOpen, Executor, Session
from manga_atlas.config import Settings, Source
from manga_atlas.match import Matcher, TitleMatch, WorkKeys, WorkNames, catalog
from manga_atlas.suwayomi import (
    MangaGone,
    SourceAPI,
    SourceFailure,
    SourceManga,
    SourceTimeout,
    SuwayomiUnavailable,
)
from manga_atlas.text import simplified

Verdict = Literal["yes", "maybe", "no", "down"]
log = logging.getLogger(__name__)


class WorkNotFound(LookupError):
    pass


@dataclass(frozen=True)
class SourceResult:
    source: Source
    state: Verdict
    query: str
    manga_id: int | None = None
    title: str | None = None
    author_match: bool | None = None
    checked_at: datetime | None = None
    cached: bool = False
    error: str | None = None
    renamed: bool = False  # the source lists it under a title the work is not known by

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "source": self.source.id,
            "name": self.source.name,
            "state": self.state,
            "query": self.query,
        }
        if self.manga_id is not None:
            out["manga_id"] = self.manga_id
            out["title"] = self.title
            out["renamed"] = self.renamed
        if self.state != "down":
            out["author_match"] = self.author_match
            out["cached"] = self.cached
        if self.checked_at is not None:
            out["checked_at"] = state.to_text(self.checked_at)
        if self.error:
            out["error"] = self.error
        return out


READABLE = ("yes", "maybe")


@dataclass
class _Budget:
    left: int  # details calls this source may still get for this work


_PREFERENCE = {True: 0, None: 1, False: 2}  # author confirmed, unknown, known to differ
_RULE_ORDER = {"exact": 0, "concat": 1, "fuzzy": 2}  # an exact title is judged before a near one


def chinese_first(sources: Sequence[Source]) -> list[Source]:
    """Configured order, Chinese-language sources ahead of the rest."""
    return sorted(sources, key=lambda s: not s.lang.startswith("zh"))


class Checker:
    def __init__(
        self,
        settings: Settings,
        client: SourceAPI,
        executor: Executor,
        matcher: Matcher,
        now: Callable[[], datetime] = state.utc_now,
    ) -> None:
        self.settings = settings
        self.client = client
        self.executor = executor
        self.matcher = matcher
        self.now = now

    # ---- one work x one source

    def _from_row(self, source: Source, query: str, row: cache.Row, keys: WorkKeys) -> SourceResult:
        return SourceResult(
            source, row.state, query, row.manga_id, row.title, row.author_match,
            row.checked_at, cached=True, renamed=self._renamed(row.title, keys),
        )  # fmt: skip

    def _renamed(self, title: str | None, keys: WorkKeys) -> bool:
        """A title that is none of the work's known titles, nor 中文名+原名 run together."""
        return bool(title) and not any(
            k in keys.keys or k in keys.concat for k in self.matcher.title_keys(str(title))
        )

    def _vouchers(self) -> list[str]:
        """Chinese sources whose titles are searched on the others: those listing authors, where
        a listing holding the author's other works too blocks a vouch."""
        return [
            s.id
            for s in self.settings.enabled_sources
            if s.lang.startswith("zh") and s.lists_authors
        ]

    def _usable(self, row: cache.Row | None, *, details: bool) -> cache.Row | None:
        if row is None or not cache.is_fresh(row, self.settings, self.now()):
            return None
        if details and row.state == "maybe" and row.author_match is None:
            return None  # a short title never confirmed by its author: ask again on the work page
        return row

    def _cached(
        self, names: WorkNames, keys: WorkKeys, source: Source, *, details: bool
    ) -> cache.Row | None:
        with state.connect(self.settings) as con:
            row = self._usable(cache.get(con, names.work_id, source.id), details=details)
            if (
                row is None
                or not source.lang.startswith("zh")
                or (row.state != "no" and not row.via)
            ):
                return row
            others = [
                (t, at)
                for t, src, at in self._listed(con, [names.work_id]).get(names.work_id, ())
                if src != source.id and self._renamed(t, keys)
            ]
        if row.state == "no" and any(at >= row.checked_at for _, at in others):
            return None  # a title learned since this miss: search again with it
        if row.via and self.matcher.key(row.via) not in {self.matcher.key(t) for t, _ in others}:
            return None  # found by a title no source vouches for any more
        return row

    def _listed(
        self, con: sqlite3.Connection, ids: Iterable[int]
    ) -> dict[int, list[tuple[str, str, datetime]]]:
        since = self.now() - timedelta(days=self.settings.cache_hit_ttl_days)
        return cache.listed(con, ids, self._vouchers(), since)

    async def check_source(
        self,
        names: WorkNames,
        keys: WorkKeys,
        source: Source,
        *,
        details: bool,
        refresh: bool = False,
        only: Sequence[str] = (),
    ) -> SourceResult:
        """`only`: search just these names (titles learned meanwhile), and ask again regardless.
        `refresh` alone (重新检查) searches the work's own names, so a source answers afresh
        rather than by a title it or another source vouched for before."""
        if refresh and not only:
            names = replace(names, learned=())
        queries = list(only) or self.matcher.queries(
            names, source.lang, thorough=details, source=source.id
        )
        if not queries:
            return SourceResult(source, "no", "")  # nothing to search with
        query = queries[0]
        refresh = refresh or bool(only)
        vouch = (
            names.name
            if source.lang.startswith("zh") and not self.matcher.chinese_titled(names)
            else None
        )
        if not refresh and (row := self._cached(names, keys, source, details=details)):
            return self._from_row(source, query, row, keys)
        gate = self.executor.gate(source.id)
        try:
            async with gate.session() as session:
                # Another request may have answered this while we queued for the source.
                if not refresh and (row := self._cached(names, keys, source, details=details)):
                    return self._from_row(source, query, row, keys)
                budget = _Budget(self.settings.match_details_max if details else 0)
                result: SourceResult | None = None
                for query in queries:  # the next name only while nothing readable turned up
                    found = await session.request(partial(self.client.search, source.id, query))
                    tried = await self._decide(
                        source, query, keys, found[: self.settings.match_top_n],
                        session, budget, vouch,
                    )  # fmt: skip
                    # The first answer stands unless a later one is readable, or names a
                    # 同名不同作 where the first found nothing.
                    if (
                        result is None
                        or tried.state in READABLE
                        or (tried.title and not result.title)
                    ):
                        result = tried
                    if result.state in READABLE:
                        break
                assert result is not None
        except BreakerOpen as e:
            return SourceResult(source, "down", query, error=f"paused {e.retry_in:.0f} s")
        except SuwayomiUnavailable:
            return SourceResult(source, "down", query, error="Suwayomi unreachable")
        except SourceTimeout:
            log.warning("%s timed out searching %r", source.name, query)
            return SourceResult(source, "down", query, error="timed out")
        except SourceFailure as e:
            log.warning("%s failed searching %r: %s", source.name, query, e)
            return SourceResult(source, "down", query, error=str(e) or "source error")
        except Exception:  # an odd reply must not break the stream for the other sources
            log.exception("%s: unexpected error searching %r", source.name, query)
            return SourceResult(source, "down", query, error="unexpected reply")
        if result.state != "down":
            # Found by a title another source vouched for, not by the work's own names: such a
            # row never vouches for that title in turn, and goes when nothing vouches for it.
            via = (
                result.query
                if result.state in READABLE
                and self.matcher.key(result.query) not in self._own(names)
                else None
            )
            with state.connect(self.settings) as con:
                cache.put(
                    con,
                    cache.Row(
                        names.work_id, source.id, result.state, result.manga_id, result.title,
                        result.author_match, result.checked_at or self.now(), via,
                    ),
                )  # fmt: skip
        return result

    def _own(self, names: WorkNames) -> set[str]:
        """Keys of every search the work's own names make, on a page check or a work page: the
        Simplified spelling of an alias need not share its key."""
        mine = replace(names, learned=())
        return {
            self.matcher.key(q)
            for thorough in (False, True)
            for lang in ("zh", "ja")
            for q in self.matcher.queries(mine, lang, thorough=thorough)
        }

    async def _decide(
        self,
        source: Source,
        query: str,
        keys: WorkKeys,
        found: Sequence[SourceManga],
        session: Session,
        budget: _Budget,
        vouch: str | None,
    ) -> SourceResult:
        """Title rules pick candidates; an author can upgrade a short title from ≈ to ✓. With none,
        and `vouch` (the work's own title) given, the top result may pass under another
        translation."""
        candidates = [(m, t) for m in found if (t := self.matcher.match_title(m.title, keys))]
        candidates.sort(key=lambda c: _RULE_ORDER[c[1].rule])  # search order within a rule
        if not candidates and vouch is not None and found:
            return await self._vouched(source, query, keys, found, session, budget, vouch)
        judged: list[tuple[SourceManga, TitleMatch, bool | None]] = []
        for manga, match in candidates:
            try:
                author = await self._creators(manga, session, budget)
            except MangaGone:
                continue  # listed in search, page gone: not readable here
            confirmed = self.matcher.author_match(author, keys)
            judged.append((manga, match, confirmed))
            if confirmed:
                break
        now = self.now()
        if not judged:
            return SourceResult(source, "no", query, checked_at=now)
        # Confirmed first, then an unknown author, then a known other one; search order within.
        manga, match, confirmed = min(judged, key=lambda j: _PREFERENCE[j[2]])
        verdict: Verdict
        if self.matcher.other_work(match, confirmed):
            verdict = "no"  # 同名不同作: listed with its title, never readable
        elif confirmed or not self.matcher.is_short(match):
            verdict = "yes"
        else:
            verdict = "maybe"
        return SourceResult(
            source, verdict, query, manga.id, manga.title, confirmed, now,
            renamed=self._renamed(manga.title, keys),
        )  # fmt: skip

    async def _vouched(
        self,
        source: Source,
        query: str,
        keys: WorkKeys,
        found: Sequence[SourceManga],
        session: Session,
        budget: _Budget,
        main: str,
    ) -> SourceResult:
        """The top result under another translation: ≈ when its author is the work's and no other
        listed result names that author (the author's other works listed too may mean this one is
        a sibling); else no."""
        now = self.now()
        top = found[0]
        if self.matcher.retitled(top.title, keys, main) is None or any(
            self.matcher.author_match(m.creators, keys) for m in found[1:]
        ):
            return SourceResult(source, "no", query, checked_at=now)
        try:
            author = await self._creators(top, session, budget)
        except MangaGone:
            return SourceResult(source, "no", query, checked_at=now)
        if not self.matcher.author_match(author, keys):
            return SourceResult(source, "no", query, checked_at=now)
        return SourceResult(source, "maybe", query, top.id, top.title, True, now, renamed=True)

    async def _creators(self, manga: SourceManga, session: Session, budget: _Budget) -> str | None:
        """Its listed authors, else (while the budget lasts) its page's; raises MangaGone."""
        if manga.creators is not None or budget.left <= 0:
            return manga.creators
        budget.left -= 1
        try:
            return (await session.request(partial(self.client.manga, manga.id))).creators
        except (SourceFailure, BreakerOpen):
            budget.left = 0  # keep the title evidence, stop asking
            return None

    # ---- work page

    def prepare(self, work_id: int) -> tuple[WorkNames, WorkKeys]:
        """Names and keys of a work; raises WorkNotFound or catalog.CatalogMissing."""
        names = self.load([work_id]).get(work_id)
        if names is None:
            raise WorkNotFound(work_id)
        return names, self.matcher.work_keys(names)

    async def check_work(
        self, names: WorkNames, keys: WorkKeys, *, refresh: bool = False
    ) -> AsyncIterator[SourceResult]:
        """Every enabled source in parallel; results in the order they finish. Then, if a title was
        learned meanwhile, the Chinese sources that found nothing are asked again with it alone (a
        failure then keeps the first answer on screen)."""
        first: list[SourceResult] = []
        async for result in self._parallel(
            [partial(self.check_source, names, keys, s, details=True, refresh=refresh)
             for s in self.settings.enabled_sources]
        ):  # fmt: skip
            first.append(result)
            yield result
        again, again_keys = self.prepare(names.work_id)
        before = set() if refresh else set(names.learned)  # 重新检查 searched none of them
        only = self.matcher.distinct(
            simplified(t) for t, src in again.learned if (t, src) not in before
        )
        retry = [r.source for r in first if r.state == "no" and r.source.lang.startswith("zh")]
        if not only or not retry:
            return
        async for result in self._parallel(
            [partial(self.check_source, again, again_keys, s, details=True, only=only)
             for s in retry]
        ):  # fmt: skip
            if result.state != "down":
                yield result

    async def _parallel(self, calls: Sequence[Callable[[], Any]]) -> AsyncIterator[SourceResult]:
        tasks = [asyncio.create_task(call()) for call in calls]
        try:
            for next_done in asyncio.as_completed(tasks):
                yield await next_done
        finally:
            for task in tasks:
                task.cancel()

    # ---- 检查本页

    async def _first_hit(
        self, names: WorkNames | None, work_id: int, sources: Sequence[Source]
    ) -> cache.Readable:
        if names is None:
            return "unknown"
        rows = self._rows(work_id)
        if not any(r.state in READABLE for r in rows if self._usable(r, details=False)):
            keys = self.matcher.work_keys(names)
            for source in sources:
                result = await self.check_source(names, keys, source, details=False)
                if result.state in READABLE:
                    break
            rows = self._rows(work_id)
        return cache.readable(rows, self.settings, self.now())

    def _rows(self, work_id: int) -> list[cache.Row]:
        """Cached rows of the enabled sources: a disabled source's verdicts don't count."""
        enabled = {s.id for s in self.settings.enabled_sources}
        with state.connect(self.settings) as con:
            cached = cache.rows(con, work_id)
        return [row for source_id, row in cached.items() if source_id in enabled]

    def load(self, work_ids: Sequence[int]) -> dict[int, WorkNames]:
        """Names of the works that exist, with their learned titles; raises
        catalog.CatalogMissing."""
        with catalog.connect(self.settings) as con:
            names = catalog.work_names(con, work_ids)
        with state.connect(self.settings) as con:
            listed = self._listed(con, names)
        out = {}
        for w, n in names.items():
            keys = self.matcher.work_keys(n)
            learned = [(t, src) for t, src, _ in listed.get(w, ()) if self._renamed(t, keys)]
            out[w] = replace(n, learned=tuple(learned))  # earliest first
        return out

    async def check_many(
        self, work_ids: Sequence[int], names: dict[int, WorkNames]
    ) -> AsyncIterator[dict[str, Any]]:
        """Progress lines {id, readable, done, total} as each work finishes."""
        ids = list(dict.fromkeys(work_ids))
        sources = chinese_first(self.settings.enabled_sources)
        # A few works at a time keep every source busy, while a work page opened meanwhile
        # waits behind only a few queued requests per source.
        slots = asyncio.Semaphore(self.settings.check_parallel_works)

        async def one(work_id: int) -> tuple[int, cache.Readable]:
            async with slots:
                return work_id, await self._first_hit(names.get(work_id), work_id, sources)

        tasks = [asyncio.create_task(one(w)) for w in ids]
        try:
            for done, next_done in enumerate(asyncio.as_completed(tasks), start=1):
                work_id, readable = await next_done
                yield {"id": work_id, "readable": readable, "done": done, "total": len(ids)}
        finally:
            for task in tasks:
                task.cancel()
