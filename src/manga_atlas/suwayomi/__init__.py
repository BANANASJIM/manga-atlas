"""Suwayomi access: the only package that talks to the Suwayomi server."""

from manga_atlas.config import Settings
from manga_atlas.suwayomi.client import (
    Category,
    GraphQLError,
    LibraryManga,
    MangaGone,
    MangaState,
    SourceAPI,
    SourceFailure,
    SourceManga,
    SourceTimeout,
    SuwayomiAPI,
    SuwayomiClient,
    SuwayomiError,
    SuwayomiUnavailable,
    manga_url,
)


def client_for(settings: Settings) -> SuwayomiClient:
    return SuwayomiClient(
        settings.suwayomi_url,
        timeout=settings.suwayomi_timeout_s,
        source_timeout=settings.source_timeout_s,
        empty_markers=settings.source_empty_markers,
        dead_markers=settings.source_dead_markers,
        no_chapters_markers=settings.source_no_chapters_markers,
    )


__all__ = [
    "Category",
    "GraphQLError",
    "LibraryManga",
    "MangaGone",
    "MangaState",
    "SourceAPI",
    "SourceFailure",
    "SourceManga",
    "SourceTimeout",
    "SuwayomiAPI",
    "SuwayomiClient",
    "SuwayomiError",
    "SuwayomiUnavailable",
    "client_for",
    "manga_url",
]
