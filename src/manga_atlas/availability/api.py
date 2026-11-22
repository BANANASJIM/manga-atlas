"""HTTP routes: availability checks (NDJSON streams), status and the way into Suwayomi."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from manga_atlas.availability.chapters import ensure_chapters
from manga_atlas.availability.checker import WorkNotFound
from manga_atlas.availability.service import Service, get_service
from manga_atlas.match.catalog import CatalogMissing
from manga_atlas.query.db import catalog_info
from manga_atlas.suwayomi import manga_url

router = APIRouter()
NDJSON = "application/x-ndjson"
ServiceDep = Annotated[Service, Depends(get_service)]


class CheckRequest(BaseModel):
    ids: list[int]


class CheckOptions(BaseModel):
    force: bool = False  # 重新检查: ask every source again, ignoring the cache


def _ndjson(items: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    async def lines() -> AsyncIterator[str]:
        async for item in items:
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type=NDJSON)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@router.post("/api/work/{work_id}/check", response_model=None)
async def check_work(
    work_id: int, service: ServiceDep, options: CheckOptions | None = None
) -> StreamingResponse | JSONResponse:
    """Body {force?}. One line per enabled source as it finishes, and another for a Chinese source
    asked again with a title learned meanwhile: {source, name, state: yes|maybe|no|down, query,
    manga_id?, title?, renamed?, author_match?, cached?, checked_at?, error?}."""
    force = options is not None and options.force
    try:
        names, keys = service.checker.prepare(work_id)
    except CatalogMissing as e:
        return _error(503, str(e))
    except WorkNotFound:
        return _error(404, "no such work")

    async def results() -> AsyncIterator[dict[str, Any]]:
        async for result in service.checker.check_work(names, keys, refresh=force):
            yield result.to_json()

    return _ndjson(results())


@router.post("/api/check", response_model=None)
async def check_page(body: CheckRequest, service: ServiceDep) -> StreamingResponse | JSONResponse:
    """检查本页: one line per work as it finishes: {id, readable, done, total}."""
    limit = service.settings.check_batch_max
    if not body.ids or len(set(body.ids)) > limit:
        return _error(422, f"send 1-{limit} ids")
    try:
        names = service.checker.load(body.ids)
    except CatalogMissing as e:
        return _error(503, str(e))
    return _ndjson(service.checker.check_many(body.ids, names))


@router.get("/api/status")
async def status(service: ServiceDep) -> dict[str, Any]:
    """{suwayomi_url, reachable, version, sources: [{id, name, lang, breaker, retry_in_s}],
    check_batch_max, catalog}."""
    return {**await service.status(), "catalog": catalog_info(service.settings)}


@router.get("/api/read/{manga_id}")
async def read(manga_id: int, service: ServiceDep) -> RedirectResponse:
    """读: 302 to the entry's page in Suwayomi, after fetching the chapters that page would not.
    A plain link, so GET: the guard in web/app.py already refuses it from other sites."""
    await ensure_chapters(service, manga_id)
    return RedirectResponse(manga_url(service.settings.suwayomi_url, manga_id), status_code=302)
