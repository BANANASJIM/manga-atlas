"""Covers: fetched from Bangumi's image endpoint on first view, then served from covers_dir.

A zero-byte file marks a work without a cover (or a failed fetch) for cover_miss_ttl_s.
"""

from __future__ import annotations

import asyncio
import os
import time
from enum import Enum
from pathlib import Path

import httpx

from manga_atlas.config import Settings

_SIGNATURES = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG", "image/png"), (b"GIF8", "image/gif"))
_HEAD = 12  # bytes needed to recognise any of the formats above


def media_type(head: bytes) -> str | None:
    for magic, kind in _SIGNATURES:
        if head.startswith(magic):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


class _State(Enum):
    HIT = 1
    MISS = 2  # known to have no cover, for now
    UNKNOWN = 3


class Covers:
    """One per app. Fetches run at most cover_concurrency at a time; requests for one cover
    share one download."""

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: httpx.AsyncClient | None = None
        self._gate: asyncio.Semaphore | None = None
        self._pending: dict[int, asyncio.Future[None]] = {}

    def path(self, work_id: int) -> Path:
        return self.settings.covers_dir / str(work_id)

    def cached(self, work_id: int) -> tuple[Path, str] | None:
        """The cover if it is on disk already."""
        path = self.path(work_id)
        if self._state(path) is not _State.HIT:
            return None
        with path.open("rb") as fh:
            kind = media_type(fh.read(_HEAD))
        return (path, kind) if kind else None

    def known_missing(self, work_id: int) -> bool:
        return self._state(self.path(work_id)) is _State.MISS

    async def fetch(self, work_id: int) -> tuple[Path, str] | None:
        """Download the cover unless it is cached or known to be missing."""
        client, gate = self._session()
        pending = self._pending.get(work_id)
        if pending is None:
            pending = asyncio.ensure_future(self._fetch(work_id, client, gate))
            self._pending[work_id] = pending
            pending.add_done_callback(lambda _: self._pending.pop(work_id, None))
        await asyncio.shield(pending)  # one tab leaving does not cancel it for the others
        return self.cached(work_id)

    async def _fetch(
        self, work_id: int, client: httpx.AsyncClient, gate: asyncio.Semaphore
    ) -> None:
        path = self.path(work_id)
        async with gate:
            if self._state(path) is _State.UNKNOWN:  # another request may have fetched it
                self._store(path, await self._download(client, work_id))

    def _state(self, path: Path) -> _State:
        try:
            st = path.stat()
        except FileNotFoundError:
            return _State.UNKNOWN
        if st.st_size:
            return _State.HIT
        fresh = time.time() - st.st_mtime < self.settings.cover_miss_ttl_s
        return _State.MISS if fresh else _State.UNKNOWN

    def _session(self) -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
        loop = asyncio.get_running_loop()
        if self._client is None or self._gate is None or self._loop is not loop:
            self._loop = loop
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self.settings.user_agent},
                timeout=self.settings.cover_timeout_s,
                follow_redirects=True,
                transport=self._transport,
            )
            self._gate = asyncio.Semaphore(self.settings.cover_concurrency)
            self._pending = {}
        return self._client, self._gate

    async def _download(self, client: httpx.AsyncClient, work_id: int) -> bytes:
        """Image bytes, or b"" when Bangumi has no cover or cannot be reached."""
        try:
            response = await client.get(self.settings.cover_url.format(id=work_id))
        except httpx.HTTPError:
            return b""
        if (
            response.status_code != httpx.codes.OK
            or response.url.path.endswith(self.settings.cover_placeholder)
            or media_type(response.content[:_HEAD]) is None
        ):
            return b""
        return response.content

    def _store(self, path: Path, data: bytes) -> None:
        if not data and self._state(path) is _State.HIT:
            return  # never trade a picture for a miss
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_bytes(data)
        os.replace(part, path)
