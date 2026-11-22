"""Tag sources beside Bangumi.

Each source matches works to its own pages by Japanese title (titles.match), keeps what it fetched
in its folder (Settings.tag_cache), fetches anew when that is older than its configured age, and at
build time gives each matched work a page and tags spelled as the source spells them, which
build/tags.py folds into the vocabulary. A new source adds a TagSource member, a module here with
its SOURCE, its settings in config.py, its rules in vocab.toml under its key, and in the web UI
its key in settings.js TAG_SOURCES and its words in words.js.
"""

from __future__ import annotations

from collections.abc import Sequence

from manga_atlas.build.schema import TagSource
from manga_atlas.build.tagsources import jawiki, yurimiru
from manga_atlas.build.tagsources.common import Log, Page, Source, Work
from manga_atlas.build.vocab import Vocab
from manga_atlas.config import Settings

SOURCES: tuple[Source, ...] = (jawiki.SOURCE, yurimiru.SOURCE)

__all__ = ["SOURCES", "Page", "Source", "Work", "collect"]


def collect(
    settings: Settings, works: Sequence[Work], vocab: Vocab, log: Log
) -> dict[TagSource, dict[int, Page]]:
    """Each enabled source's pages for the catalogue, refreshed first when stale."""
    out: dict[TagSource, dict[int, Page]] = {}
    for source in SOURCES:
        if source.enabled(settings):
            source.refresh(settings, works, log)
            out[source.tag] = source.pages(settings, works, vocab, log)
    return out
