"""HTTP routes: search, suggest, work detail and covers (all read-only)."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi import Query as Param
from fastapi.responses import FileResponse, JSONResponse

from manga_atlas.config import Settings
from manga_atlas.query.cover import Covers
from manga_atlas.query.db import CatalogMissing, Handle, open_catalog
from manga_atlas.query.search import SORTS, Query, parse_years, search
from manga_atlas.query.suggest import suggest
from manga_atlas.query.work import work

router = APIRouter()
_LIST = re.compile(r"[,，]")


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _catalog(settings: Settings) -> Handle:
    try:
        return open_catalog(settings)
    except CatalogMissing as e:
        raise HTTPException(status_code=503, detail=str(e)) from None


def _split(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _LIST.split(value) if part.strip())


@router.get("/api/search")
def api_search(
    request: Request,
    q: str = "",
    t: str = "",
    x: str = "",
    a: str = "",
    y: str = "",
    st: str = "",
    region: str = "",
    min_score: float | None = Param(None, alias="min"),
    read: str = "",
    fav: str = "",
    sort: str = "rel",
    offset: int = Param(0, ge=0),
    limit: int | None = Param(None, ge=1),
) -> JSONResponse:
    settings = _settings(request)
    if sort not in SORTS:
        raise HTTPException(status_code=422, detail=f"sort: one of {', '.join(SORTS)}")
    try:
        year_from, year_to = parse_years(y) if y.strip() else (None, None)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="y: like 2010-2020, 2010-, -2000 or 2015"
        ) from None
    try:
        people = tuple(int(p) for p in _split(a))
    except ValueError:
        raise HTTPException(status_code=422, detail="a: person ids, comma separated") from None
    query = Query(
        text=q,
        include=_split(t),
        exclude=_split(x),
        people=people,
        year_from=year_from,
        year_to=year_to,
        statuses=_split(st),
        regions=_split(region),
        min_score=min_score,
        readable=read == "1",
        favorite=fav == "1",
        sort=sort,
        offset=offset,
        limit=min(limit or settings.search_limit, settings.search_max_limit),
    )
    return JSONResponse(search(_catalog(settings), settings, query))


@router.get("/api/suggest")
def api_suggest(request: Request, q: str = "") -> JSONResponse:
    settings = _settings(request)
    return JSONResponse(suggest(_catalog(settings), settings, q))


@router.get("/api/work/{work_id}")
def api_work(request: Request, work_id: int) -> JSONResponse:
    settings = _settings(request)
    found = work(_catalog(settings), settings, work_id)
    if found is None:
        raise HTTPException(status_code=404, detail="no such work")
    return JSONResponse(found)


@router.get("/cover/{work_id}")
async def cover(request: Request, work_id: int) -> FileResponse:
    settings = _settings(request)
    covers: Covers | None = getattr(request.app.state, "covers", None)
    if covers is None:
        covers = request.app.state.covers = Covers(settings)
    hit = covers.cached(work_id)
    if hit is None and not covers.known_missing(work_id) and _in_catalog(settings, work_id):
        hit = await covers.fetch(work_id)
    if hit is None:
        raise HTTPException(status_code=404)
    path, kind = hit
    return FileResponse(path, media_type=kind)


def _in_catalog(settings: Settings, work_id: int) -> bool:
    """Only fetch covers of catalogue works: no open proxy, nothing excluded."""
    try:
        h = open_catalog(settings)
    except CatalogMissing:
        return False
    return h.con.execute("SELECT 1 FROM work WHERE id = ?", (work_id,)).fetchone() is not None
