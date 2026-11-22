"""App factory: request guards, API routers and the static UI."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from manga_atlas.availability.api import router as availability_router
from manga_atlas.config import Settings, load
from manga_atlas.favorites.api import router as favorites_router
from manga_atlas.library.api import router as library_router
from manga_atlas.query.api import router as query_router

STATIC_DIR = Path(__file__).parent / "static"
WRITE_HEADER = "x-atlas"
SAFE_METHODS = frozenset({"GET", "HEAD"})
API_PREFIX = "/api/"
DATA_PREFIXES = (API_PREFIX, "/cover/")  # not pages; a cover may be fetched from Bangumi


def _page_navigation(request: Request) -> bool:
    """The user following a link from another site to a UI page (a click, not a script)."""
    headers = request.headers
    return (
        headers.get("sec-fetch-mode") == "navigate"
        and headers.get("sec-fetch-dest", "document") == "document"
        and headers.get("sec-fetch-user") == "?1"
        and not request.url.path.startswith(DATA_PREFIXES)
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load()
    app = FastAPI(title="MangaAtlas", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    allowed_hosts = {f"{settings.host}:{settings.port}", f"localhost:{settings.port}"}

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Host check blocks DNS rebinding; the custom header forces a CORS preflight,
        # which we never answer, so other sites cannot trigger writes.
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"error": "bad host"}, status_code=421)
        # Other sites may offer links to pages, but not embed, fetch or redirect: an <img> of
        # /cover/ would fetch from Bangumi on their behalf, a scripted redirect to a work page
        # would start a check.
        if request.headers.get("sec-fetch-site") == "cross-site" and not _page_navigation(request):
            return JSONResponse({"error": "cross-site request"}, status_code=403)
        if request.method not in SAFE_METHODS and request.headers.get(WRITE_HEADER) != "1":
            return JSONResponse({"error": "missing X-Atlas header"}, status_code=403)
        return await call_next(request)

    app.include_router(query_router)
    app.include_router(availability_router)
    app.include_router(library_router)
    app.include_router(favorites_router)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        index = STATIC_DIR / "index.html"
        if f"/{path}".startswith(API_PREFIX) or not index.exists():
            raise HTTPException(status_code=404)
        return FileResponse(index)

    return app
