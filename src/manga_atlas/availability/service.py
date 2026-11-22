"""One availability service per app: one Suwayomi client and executor, so pacing is process-wide."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import Request

from manga_atlas.availability.checker import Checker
from manga_atlas.availability.executor import Executor
from manga_atlas.config import Settings
from manga_atlas.match import Matcher
from manga_atlas.suwayomi import SuwayomiAPI, SuwayomiError, client_for

ClientFactory = Callable[[Settings], SuwayomiAPI]


class Service:
    def __init__(self, settings: Settings, client: SuwayomiAPI) -> None:
        self.settings = settings
        self.client = client
        self.matcher = Matcher(settings)
        self.executor = Executor(settings)
        self.checker = Checker(settings, client, self.executor, self.matcher)
        self.loop = asyncio.get_running_loop()
        self.library_lock = asyncio.Lock()
        self.library_synced: float | None = None  # loop time of the last successful sync

    async def status(self) -> dict[str, Any]:
        """Suwayomi reachability and each enabled source's breaker."""
        try:
            version: str | None = await self.client.ping()
        except SuwayomiError:
            version = None
        sources = [
            {"id": s.id, "name": s.name, "lang": s.lang, **self.executor.gate(s.id).describe()}
            for s in self.settings.enabled_sources
        ]
        return {
            "suwayomi_url": self.settings.suwayomi_url,
            "reachable": version is not None,
            "version": version,
            "sources": sources,
            "check_batch_max": self.settings.check_batch_max,  # works per /api/check request
        }


async def get_service(request: Request) -> Service:
    """FastAPI dependency. Created on first use inside the running loop (async, so no race)."""
    app_state = request.app.state
    service: Service | None = getattr(app_state, "availability_service", None)
    if service is None or service.loop is not asyncio.get_running_loop():
        settings: Settings = app_state.settings
        factory: ClientFactory = getattr(app_state, "suwayomi_client_factory", client_for)
        service = Service(settings, factory(settings))
        app_state.availability_service = service
    return service
