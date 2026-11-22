"""Per-source pacing: one request in flight per source, spacing, a timeout and a breaker.

Different sources run in parallel; requests to one source queue in arrival order. A breaker
opens after `breaker_failures` consecutive failures and fails fast for `breaker_cooldown_s`;
the first request after the cooldown decides whether it closes again.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from manga_atlas.config import Settings
from manga_atlas.suwayomi import MangaGone, SourceFailure, SourceTimeout

T = TypeVar("T")
Clock = Callable[[], float]


class BreakerOpen(Exception):
    def __init__(self, source_id: str, retry_in: float) -> None:
        super().__init__(f"source {source_id} paused for {retry_in:.0f} s")
        self.retry_in = retry_in


class SourceGate:
    def __init__(self, source_id: str, settings: Settings, clock: Clock = time.monotonic) -> None:
        self.source_id = source_id
        self.settings = settings
        self.clock = clock
        self.failures = 0
        self._open_until = 0.0
        self._next_start = 0.0
        self._lock = asyncio.Lock()

    def retry_in(self) -> float:
        return max(0.0, self._open_until - self.clock())

    def describe(self) -> dict[str, Any]:
        retry = self.retry_in()
        return {"breaker": "open" if retry else "closed", "retry_in_s": round(retry, 1)}

    def _check(self) -> None:
        if retry := self.retry_in():
            raise BreakerOpen(self.source_id, retry)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Session]:
        """Hold this source for a short sequence of requests (search, then details)."""
        self._check()
        await self._lock.acquire()
        session = Session(self)
        try:
            self._check()
            yield session
        finally:
            call = session.call
            if call is None or call.done():
                self._lock.release()
            else:  # the caller left mid-call: Suwayomi is still asking the site
                call.add_done_callback(self._release_after)

    def _release_after(self, call: asyncio.Future[Any]) -> None:
        if not call.cancelled():
            call.exception()  # nobody wants the outcome any more
        self._lock.release()

    def _settle(self, start: float, *, failed: bool, timed_out: bool = False) -> None:
        end = self.clock()
        next_start = end + self.settings.source_interval_s
        if timed_out:  # Suwayomi may still be talking to the site; don't ask it twice at once
            next_start = max(next_start, start + self.settings.suwayomi_source_timeout_s)
        self._next_start = max(self._next_start, next_start)
        if failed:
            self.failures += 1
            if self.failures >= self.settings.breaker_failures:
                self._open_until = end + self.settings.breaker_cooldown_s


class Session:
    def __init__(self, gate: SourceGate) -> None:
        self._gate = gate
        self.call: asyncio.Future[Any] | None = None  # the latest request, run as its own task

    async def request(self, op: Callable[[], Awaitable[T]]) -> T:
        gate = self._gate
        gate._check()
        wait = gate._next_start - gate.clock()
        if wait > 0:
            await asyncio.sleep(wait)
        # A cancelled caller stops waiting, not the call: the session keeps the source until
        # the call itself ends (see SourceGate.session), so no later request can overlap it.
        call = self.call = asyncio.ensure_future(self._call(op))
        return await asyncio.shield(call)

    async def _call(self, op: Callable[[], Awaitable[T]]) -> T:
        gate = self._gate
        start = gate.clock()
        try:
            async with asyncio.timeout(gate.settings.source_timeout_s):
                result = await op()
        except TimeoutError as e:
            gate._settle(start, failed=True, timed_out=True)
            raise SourceTimeout("timed out") from e
        except SourceTimeout:
            gate._settle(start, failed=True, timed_out=True)
            raise
        except SourceFailure:
            gate._settle(start, failed=True)
            raise
        except MangaGone:  # the site answered: it is up
            gate.failures = 0
            gate._settle(start, failed=False)
            raise
        except BaseException:  # Suwayomi down: no verdict on the source
            gate._settle(start, failed=False)
            raise
        gate.failures = 0
        gate._settle(start, failed=False)
        return result


class Executor:
    """One gate per configured source."""

    def __init__(self, settings: Settings, clock: Clock = time.monotonic) -> None:
        self.gates = {s.id: SourceGate(s.id, settings, clock) for s in settings.sources}

    def gate(self, source_id: str) -> SourceGate:
        return self.gates[source_id]
