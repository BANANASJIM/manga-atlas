"""HTTP routes: add and remove a favorite."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from manga_atlas.availability import state
from manga_atlas.query.api import _catalog, _settings

router = APIRouter()


@router.put("/api/favorites/{work_id}", status_code=204)
def add(request: Request, work_id: int) -> None:
    """Adding a favorite again keeps the time it was first added."""
    settings = _settings(request)
    h = _catalog(settings)
    if h.con.execute("SELECT 1 FROM work WHERE id = ?", (work_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="no such work")
    with state.connect(settings) as con:
        con.execute(
            "INSERT OR IGNORE INTO favorite VALUES (?, ?)",
            (work_id, state.to_text(state.utc_now())),
        )


@router.delete("/api/favorites/{work_id}", status_code=204)
def remove(request: Request, work_id: int) -> None:
    with state.connect(_settings(request)) as con:
        con.execute("DELETE FROM favorite WHERE work_id = ?", (work_id,))
