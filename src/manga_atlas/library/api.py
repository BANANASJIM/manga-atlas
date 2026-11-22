"""HTTP routes: the Suwayomi library mirror and 整理书架."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from manga_atlas.availability import state
from manga_atlas.availability.service import Service, get_service
from manga_atlas.library.organize import clean_tags, organize
from manga_atlas.library.sync import entries, sync
from manga_atlas.match.catalog import CatalogMissing
from manga_atlas.suwayomi import SuwayomiError, SuwayomiUnavailable

router = APIRouter()
ServiceDep = Annotated[Service, Depends(get_service)]


class OrganizeRequest(BaseModel):
    tags: list[str]
    apply: bool = False


@router.get("/api/library")
async def library(service: ServiceDep) -> dict[str, Any]:
    """{reachable, items: [{manga_id, source_id, source, title, work_id, synced_at}]}.
    Served from the mirror when Suwayomi is unreachable."""
    try:
        await sync(service)
        reachable = True
    except SuwayomiError:
        reachable = False
    names = {s.id: s.name for s in service.settings.sources}
    items = [
        {
            "manga_id": e.manga_id,
            "source_id": e.source_id,
            "source": names.get(e.source_id),
            "title": e.title,
            "work_id": e.work_id,
            "synced_at": state.to_text(e.synced_at),
        }
        for e in entries(service.settings)
    ]
    return {"reachable": reachable, "items": items}


@router.post("/api/organize", response_model=None)
async def organize_shelf(
    body: OrganizeRequest, service: ServiceDep
) -> dict[str, Any] | JSONResponse:
    """{tags, apply:false} previews categories -> entries; apply:true writes them."""
    if not clean_tags(body.tags):
        return JSONResponse({"error": "no tags"}, status_code=422)
    try:
        return await organize(service, body.tags, apply=body.apply)
    except CatalogMissing as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except SuwayomiUnavailable:
        return JSONResponse({"error": "Suwayomi unreachable"}, status_code=503)
    except SuwayomiError as e:
        return JSONResponse({"error": str(e) or "Suwayomi error"}, status_code=502)
