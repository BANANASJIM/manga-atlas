"""Availability checks against Suwayomi sources."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncIterator
from typing import Any

import httpx

from manga_atlas.config import Settings


async def warm_async(
    settings: Settings, top: int, transport: httpx.AsyncBaseTransport | None = None
) -> int:
    """Pre-check the `top` most collected works. The running app does the checking, so its
    pacing and breakers cover these requests too."""
    from manga_atlas.match import catalog

    try:
        with catalog.connect(settings) as con:
            ids = catalog.popular_work_ids(con, top)
    except catalog.CatalogMissing:
        print("catalogue not built: run `manga-atlas build` first")
        return 1
    base = f"http://{settings.host}:{settings.port}"
    timeout = settings.app_request_timeout_s  # a page check's lines may be minutes apart
    async with httpx.AsyncClient(base_url=base, transport=transport, timeout=timeout) as app:
        try:
            status = await app.get("/api/status")
            status.raise_for_status()
            info = status.json()
            if not info.get("reachable"):
                print(f"Suwayomi unreachable (asked the app at {base})")
                return 1
            return await _report(_check(app, ids, int(info["check_batch_max"])))
        except httpx.ConnectError:
            print(
                f"MangaAtlas is not running at {base}: start it with `manga-atlas serve`"
                " or `systemctl --user start manga-atlas`"
            )
            return 1
        except httpx.HTTPError as e:
            print(f"the app at {base} did not answer: {e!r}")
            return 1


async def _check(
    app: httpx.AsyncClient, ids: list[int], size: int
) -> AsyncIterator[dict[str, Any]]:
    """Progress lines of the app's page checks, at most `size` works per request."""
    from manga_atlas.web.app import WRITE_HEADER

    for start in range(0, len(ids), size):
        async with app.stream(
            "POST",
            "/api/check",
            json={"ids": ids[start : start + size]},
            headers={WRITE_HEADER: "1"},
        ) as response:
            response.raise_for_status()
            async for text in response.aiter_lines():
                if text.strip():
                    line = json.loads(text)
                    yield line | {"done": start + line["done"], "total": len(ids)}


async def _report(lines: AsyncIterator[dict[str, Any]]) -> int:
    tally: Counter[str] = Counter()
    async for line in lines:
        tally[line["readable"]] += 1
        print(f"[{line['done']}/{line['total']}] {line['id']} {line['readable']}", flush=True)
    print(" ".join(f"{verdict}={n}" for verdict, n in sorted(tally.items())))
    return 0


def warm(settings: Settings, top: int) -> int:
    """Pre-check the `top` most collected works through the running app (manual, optional)."""
    return asyncio.run(warm_async(settings, top))
