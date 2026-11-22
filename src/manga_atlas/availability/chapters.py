"""Chapters for an entry about to be read in Suwayomi's web UI.

The web UI fetches an entry's chapters itself only while the entry is uninitialized. An
availability check's details call brings the chapters along, but an entry initialized without
them anyway (by an older check, or when the chapter fetch failed) would open empty. So it gets its
chapter list here first, through its source's gate like every other request to that source.
"""

from __future__ import annotations

import logging
from functools import partial

from manga_atlas.availability.executor import BreakerOpen
from manga_atlas.availability.service import Service
from manga_atlas.suwayomi import SuwayomiError

log = logging.getLogger(__name__)


async def ensure_chapters(service: Service, manga_id: int) -> None:
    """Fetch the chapter list of an initialized entry that has none. Never raises: whatever goes
    wrong, the caller still sends the user to Suwayomi."""
    client = service.client
    try:
        entry = await client.manga_state(manga_id)
        if not entry.initialized or entry.chapter_count:
            return  # nothing to add, or the web UI does the whole refresh itself
        gate = service.executor.gates.get(entry.source_id)
        if gate is None:
            return  # a source this app does not pace is never asked
        async with gate.session() as session:
            # Another tab may have fetched them while this one queued for the source.
            if (await client.manga_state(manga_id)).chapter_count:
                return
            await session.request(partial(client.fetch_chapters, manga_id))
    except (BreakerOpen, SuwayomiError) as e:
        log.warning("manga %s: chapters not fetched: %r", manga_id, e)
    except Exception:  # an odd reply must not keep the entry from opening
        log.exception("manga %s: unexpected error fetching chapters", manga_id)
