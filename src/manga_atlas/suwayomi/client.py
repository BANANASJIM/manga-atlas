"""Async GraphQL client: the only code that talks to Suwayomi.

Searching is fetchSourceManga (Suwayomi caches what it returns; we never add to the library).
A details call brings the entry's chapter list along, and opening an entry that still has none
fetches it (fetchChapters): both fill that cache the same way. Writes are limited to creating
categories and adding library entries to them, and only the organize "apply" path calls those.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

GRAPHQL_PATH = "/api/graphql"
_FETCH_PREFIX = re.compile(r"^Exception while fetching data \([^)]*\) : ")

_MANGA_FIELDS = "id title author artist sourceId"
_SEARCH = (
    "mutation Search($input: FetchSourceMangaInput!) {"
    f" fetchSourceManga(input: $input) {{ mangas {{ {_MANGA_FIELDS} }} }} }}"
)
_DETAILS = (
    "mutation Details($input: FetchMangaAndChaptersInput!) {"
    f" fetchMangaAndChapters(input: $input) {{ manga {{ {_MANGA_FIELDS} }} }} }}"
)
_MANGA_STATE = (
    "query MangaState($id: Int!) {"
    " manga(id: $id) { sourceId initialized chapters { totalCount } } }"
)
_FETCH_CHAPTERS = (
    "mutation FetchChapters($input: FetchChaptersInput!) {"
    " fetchChapters(input: $input) { chapters { id } } }"
)
_LIBRARY = (
    "query Library { mangas(condition: {inLibrary: true}) { nodes {"
    " id title author artist sourceId categories { nodes { id } } } } }"
)
_CATEGORIES = "query Categories { categories { nodes { id name } } }"
_CREATE_CATEGORY = (
    "mutation CreateCategory($input: CreateCategoryInput!) {"
    " createCategory(input: $input) { category { id name } } }"
)
_ADD_TO_CATEGORY = (
    "mutation AddToCategory($input: UpdateMangasCategoriesInput!) {"
    " updateMangasCategories(input: $input) { mangas { id } } }"
)
_SOURCE = "query Source($id: LongString!) { source(id: $id) { id } }"
_PING = "query Ping { aboutServer { version } }"


class SuwayomiError(Exception):
    """Base for everything this client raises."""


class SuwayomiUnavailable(SuwayomiError):
    """Suwayomi itself did not answer usefully (not running, refused, broken reply)."""


class GraphQLError(SuwayomiError):
    """Suwayomi answered a call that never reaches a source with an error."""


class SourceFailure(SuwayomiError):
    """A source behind Suwayomi failed; counts toward that source's breaker."""


class SourceTimeout(SourceFailure):
    """No answer from the source in time."""


class MangaGone(SuwayomiError):
    """The source still lists the entry in search, but its page is gone."""


@dataclass(frozen=True)
class SourceManga:
    id: int
    title: str
    author: str | None
    artist: str | None
    source_id: str

    @property
    def creators(self) -> str | None:
        names = [n for n in (self.author, self.artist) if n]
        return ", ".join(dict.fromkeys(names)) or None


@dataclass(frozen=True)
class MangaState:
    """What Suwayomi's own database holds about an entry."""

    source_id: str
    initialized: bool
    chapter_count: int


@dataclass(frozen=True)
class LibraryManga:
    id: int
    title: str
    author: str | None
    artist: str | None
    source_id: str
    category_ids: tuple[int, ...]

    @property
    def creators(self) -> str | None:
        names = [n for n in (self.author, self.artist) if n]
        return ", ".join(dict.fromkeys(names)) or None


@dataclass(frozen=True)
class Category:
    id: int
    name: str


class SourceAPI(Protocol):
    """The calls that reach a manga site (search, details, chapter lists)."""

    async def search(self, source_id: str, query: str) -> list[SourceManga]: ...

    async def manga(self, manga_id: int) -> SourceManga: ...

    async def fetch_chapters(self, manga_id: int) -> int: ...


class SuwayomiAPI(SourceAPI, Protocol):
    """Everything the app asks of Suwayomi; tests substitute a fake."""

    async def library(self) -> list[LibraryManga]: ...

    async def categories(self) -> list[Category]: ...

    async def create_category(self, name: str) -> Category: ...

    async def add_to_category(self, category_id: int, manga_ids: Iterable[int]) -> None: ...

    async def manga_state(self, manga_id: int) -> MangaState: ...

    async def source_exists(self, source_id: str) -> bool: ...

    async def ping(self) -> str: ...

    async def aclose(self) -> None: ...


def _manga(d: dict[str, Any]) -> SourceManga:
    return SourceManga(
        id=int(d["id"]),
        title=d.get("title") or "",
        author=d.get("author") or None,
        artist=d.get("artist") or None,
        source_id=str(d.get("sourceId") or ""),
    )


def manga_url(base_url: str, manga_id: int) -> str:
    """Where Suwayomi's own web UI shows one entry."""
    return f"{base_url.rstrip('/')}/manga/{manga_id}"


def _first_line(message: str) -> str:
    line = (message.strip().splitlines() or [""])[0]
    return _FETCH_PREFIX.sub("", line).strip()


class SuwayomiClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float,
        source_timeout: float,
        empty_markers: Iterable[str] = (),
        dead_markers: Iterable[str] = (),
        no_chapters_markers: Iterable[str] = (),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._source_timeout = source_timeout
        self._empty = tuple(empty_markers)
        self._dead = tuple(dead_markers)
        self._no_chapters = tuple(no_chapters_markers)
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        reaches_source: bool = False,
    ) -> dict[str, Any]:
        """POST one GraphQL request; returns `data`, raises GraphQLError on `errors`."""
        payload = {"query": query, "variables": variables or {}}
        kwargs: dict[str, Any] = {"json": payload}
        if reaches_source:
            kwargs["timeout"] = self._source_timeout
        try:
            response = await self._http.post(GRAPHQL_PATH, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise SuwayomiUnavailable(f"cannot connect to {self.base_url}") from e
        except httpx.TimeoutException as e:
            if reaches_source:
                raise SourceTimeout("timed out") from e
            raise SuwayomiUnavailable("Suwayomi timed out") from e
        except httpx.HTTPError as e:
            raise SuwayomiUnavailable(f"{type(e).__name__}: {e}") from e
        try:
            body = response.json()
        except ValueError as e:
            raise SuwayomiUnavailable(f"HTTP {response.status_code}, not JSON") from e
        if not isinstance(body, dict):
            raise SuwayomiUnavailable(f"HTTP {response.status_code}, unexpected reply")
        errors = body.get("errors")
        if errors:
            message = (
                errors[0].get("message", "") if isinstance(errors[0], dict) else str(errors[0])
            )
            raise GraphQLError(_first_line(str(message)) or "unknown error")
        data = body.get("data")
        if response.status_code >= 400 or not isinstance(data, dict):
            raise SuwayomiUnavailable(f"HTTP {response.status_code}")
        return data

    # ---- sources (each call reaches a manga site; the caller paces them)

    async def search(self, source_id: str, query: str) -> list[SourceManga]:
        """First page of a source's search results, in the source's order."""
        variables = {"input": {"source": source_id, "type": "SEARCH", "page": 1, "query": query}}
        try:
            data = await self._call(_SEARCH, variables, reaches_source=True)
        except GraphQLError as e:
            if any(marker in str(e) for marker in self._empty):
                return []
            raise SourceFailure(str(e)) from e
        return [_manga(m) for m in data["fetchSourceManga"]["mangas"]]

    async def manga(self, manga_id: int) -> SourceManga:
        """Fresh details of one entry (author, artist), with its chapter list: Suwayomi's web UI
        fetches chapters itself only for entries never fetched, so details alone open empty."""
        try:
            return await self._details(manga_id, chapters=True)
        except GraphQLError as e:
            if not self._no_chapters_listed(e):
                raise self._entry_error(e) from e
        try:  # the source lists the entry without chapters: ask for its details alone
            return await self._details(manga_id, chapters=False)
        except GraphQLError as e:
            raise self._entry_error(e) from e

    async def _details(self, manga_id: int, *, chapters: bool) -> SourceManga:
        variables = {"input": {"id": manga_id, "fetchManga": True, "fetchChapters": chapters}}
        data = await self._call(_DETAILS, variables, reaches_source=True)
        return _manga(data["fetchMangaAndChapters"]["manga"])

    async def fetch_chapters(self, manga_id: int) -> int:
        """Have Suwayomi fetch an entry's chapter list from its source; returns how many."""
        variables = {"input": {"mangaId": manga_id}}
        try:
            data = await self._call(_FETCH_CHAPTERS, variables, reaches_source=True)
        except GraphQLError as e:
            if self._no_chapters_listed(e):
                return 0
            raise self._entry_error(e) from e
        return len(data["fetchChapters"]["chapters"])

    def _entry_error(self, e: GraphQLError) -> SourceFailure | MangaGone:
        """A source's error about one entry: its page is gone, or the source failed."""
        if any(marker in str(e) for marker in self._dead):
            return MangaGone(str(e))
        return SourceFailure(str(e))

    def _no_chapters_listed(self, e: GraphQLError) -> bool:
        """The source says the entry has no chapters (Suwayomi reports that as an error)."""
        return any(marker in str(e) for marker in self._no_chapters)

    # ---- Suwayomi's own database (no source traffic)

    async def manga_state(self, manga_id: int) -> MangaState:
        """Whether Suwayomi has initialized an entry, and how many chapters it holds."""
        data = await self._call(_MANGA_STATE, {"id": manga_id})
        m = data["manga"]
        return MangaState(
            source_id=str(m["sourceId"]),
            initialized=bool(m["initialized"]),
            chapter_count=int(m["chapters"]["totalCount"]),
        )

    async def library(self) -> list[LibraryManga]:
        data = await self._call(_LIBRARY)
        return [
            LibraryManga(
                id=int(m["id"]),
                title=m.get("title") or "",
                author=m.get("author") or None,
                artist=m.get("artist") or None,
                source_id=str(m.get("sourceId") or ""),
                category_ids=tuple(int(c["id"]) for c in m["categories"]["nodes"]),
            )
            for m in data["mangas"]["nodes"]
        ]

    async def categories(self) -> list[Category]:
        data = await self._call(_CATEGORIES)
        return [Category(int(c["id"]), c["name"]) for c in data["categories"]["nodes"]]

    async def create_category(self, name: str) -> Category:
        data = await self._call(_CREATE_CATEGORY, {"input": {"name": name}})
        c = data["createCategory"]["category"]
        return Category(int(c["id"]), c["name"])

    async def add_to_category(self, category_id: int, manga_ids: Iterable[int]) -> None:
        """Add entries to a category, keeping every category they already have."""
        ids = sorted(set(manga_ids))
        if ids:
            patch = {"addToCategories": [category_id]}
            await self._call(_ADD_TO_CATEGORY, {"input": {"ids": ids, "patch": patch}})

    async def source_exists(self, source_id: str) -> bool:
        try:
            await self._call(_SOURCE, {"id": source_id})
        except GraphQLError:
            return False
        return True

    async def ping(self) -> str:
        """Suwayomi's version; raises SuwayomiUnavailable when it is not answering."""
        data = await self._call(_PING)
        return str(data["aboutServer"]["version"])
