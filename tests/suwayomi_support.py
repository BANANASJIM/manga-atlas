"""Shared test doubles: a scripted Suwayomi and a tiny atlas.db on the real schema."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from manga_atlas.build.schema import Role
from manga_atlas.suwayomi import (
    Category,
    LibraryManga,
    MangaState,
    SourceManga,
    SuwayomiUnavailable,
)
from tests.atlas_support import Atlas


@dataclass
class Work:
    id: int
    name: str
    name_cn: str = ""
    aliases: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    collects: int = 0
    tags: dict[str, int] = field(default_factory=dict)  # tag name -> votes (0: official)
    publishers: tuple[str, ...] = ()


def make_atlas(path: Path, works: Iterable[Work]) -> None:
    """The atlas.db at `path` (named atlas.db) holding `works`, on the real schema."""
    atlas = Atlas(path.parent)
    person_ids: dict[str, int] = {}
    tag_ids: dict[str, int] = {}
    for w in works:
        atlas.work(
            w.id, name=w.name, name_cn=w.name_cn, title=w.name_cn or w.name, collects=w.collects
        )
        for alias in (w.name, w.name_cn, *w.aliases):
            if alias:
                atlas.title_alias(w.id, alias)
        credits = [(a, Role.AUTHOR) for a in w.authors] + [
            (p, Role.PUBLISHER) for p in w.publishers
        ]
        for person, role in credits:
            if person not in person_ids:
                person_ids[person] = len(person_ids) + 1
                atlas.person(person_ids[person], person)
            atlas.credit(w.id, person_ids[person], role)
        for tag, votes in w.tags.items():
            if tag not in tag_ids:
                tag_ids[tag] = len(tag_ids) + 1
                atlas.tag(tag_ids[tag], tag)
            atlas.tagged(w.id, tag_ids[tag], votes, official=votes == 0)
    atlas.save()


def manga(mid: int, title: str, source: str = "s1", author: str | None = None) -> SourceManga:
    return SourceManga(mid, title, author, None, source)


@dataclass
class Call:
    kind: str
    arg: str
    start: float
    end: float = 0.0


class FakeSuwayomi:
    """Scripted answers; records every call with timestamps."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.down = False
        self.searches: dict[tuple[str, str], list[SourceManga] | Exception] = {}
        self.default_search: list[SourceManga] = []
        self.details: dict[int, SourceManga | Exception] = {}
        self.states: dict[int, MangaState | Exception] = {}
        self.fetches: dict[int, int | Exception] = {}  # chapters a fetch brings in, or its failure
        self.items: list[LibraryManga] = []
        self.cats: list[Category] = [Category(0, "Default")]
        self.calls: list[Call] = []
        self.assigned: list[tuple[int, list[int]]] = []

    async def _enter(self, kind: str, arg: str) -> Call:
        call = Call(kind, arg, time.monotonic())
        self.calls.append(call)
        if self.delay:
            await asyncio.sleep(self.delay)
        call.end = time.monotonic()
        if self.down:
            raise SuwayomiUnavailable("down")
        return call

    def of(self, kind: str) -> list[Call]:
        return [c for c in self.calls if c.kind == kind]

    async def search(self, source_id: str, query: str) -> list[SourceManga]:
        await self._enter("search", f"{source_id}:{query}")
        answer = self.searches.get((source_id, query), self.default_search)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def manga(self, manga_id: int) -> SourceManga:
        await self._enter("manga", str(manga_id))
        answer = self.details[manga_id]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def manga_state(self, manga_id: int) -> MangaState:
        await self._enter("manga_state", str(manga_id))
        answer = self.states[manga_id]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def fetch_chapters(self, manga_id: int) -> int:
        await self._enter("fetch_chapters", str(manga_id))
        answer = self.fetches[manga_id]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(state := self.states.get(manga_id), MangaState):
            self.states[manga_id] = replace(state, chapter_count=answer)
        return answer

    async def library(self) -> list[LibraryManga]:
        await self._enter("library", "")
        return list(self.items)

    async def categories(self) -> list[Category]:
        await self._enter("categories", "")
        return list(self.cats)

    async def create_category(self, name: str) -> Category:
        await self._enter("create_category", name)
        cat = Category(max(c.id for c in self.cats) + 1, name)
        self.cats.append(cat)
        return cat

    async def add_to_category(self, category_id: int, manga_ids: Iterable[int]) -> None:
        ids = sorted(set(manga_ids))
        await self._enter("add_to_category", f"{category_id}:{ids}")
        if not ids:
            return
        self.assigned.append((category_id, ids))
        self.items = [
            LibraryManga(m.id, m.title, m.author, m.artist, m.source_id,
                         (*m.category_ids, category_id) if m.id in ids else m.category_ids)
            for m in self.items
        ]  # fmt: skip

    async def source_exists(self, source_id: str) -> bool:
        await self._enter("source_exists", source_id)
        return True

    async def ping(self) -> str:
        await self._enter("ping", "")
        return "v-test"

    async def aclose(self) -> None:
        pass
