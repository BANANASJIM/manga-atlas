"""整理书架: file library entries into Suwayomi categories named after Bangumi tags.

Preview by default. Apply creates missing categories and adds entries to them; it never
removes a category from an entry and never adds anything to the library.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from manga_atlas.availability.service import Service
from manga_atlas.config import Settings
from manga_atlas.library.sync import entries, sync
from manga_atlas.match import catalog
from manga_atlas.suwayomi import Category, LibraryManga
from manga_atlas.text import norm


@dataclass
class Group:
    tag: str  # as requested
    name: str  # category name: the tag's display name
    known: bool  # the tag exists in the catalogue
    category: Category | None
    members: list[LibraryManga] = field(default_factory=list)


def clean_tags(tags: Sequence[str]) -> list[str]:
    cleaned = (t.strip().lstrip("#").strip() for t in tags)
    return list(dict.fromkeys(t for t in cleaned if t))


def _resolve(settings: Settings, tags: Sequence[str]) -> list[tuple[str, str | None, set[int]]]:
    with catalog.connect(settings) as con:
        out: list[tuple[str, str | None, set[int]]] = []
        for tag in tags:
            found = catalog.tag_work_ids(con, tag, settings.tag_min_votes)
            out.append((tag, found[0], found[1]) if found else (tag, None, set()))
        return out


async def organize(service: Service, tags: Sequence[str], *, apply: bool) -> dict[str, Any]:
    mangas = await sync(service, force=True) or []
    resolved = await asyncio.to_thread(_resolve, service.settings, clean_tags(tags))
    work_of = {e.manga_id: e.work_id for e in entries(service.settings)}
    by_key = {norm(c.name): c for c in await service.client.categories()}

    groups: dict[str, Group] = {}
    for tag, name, works in resolved:
        label = name or tag
        group = groups.setdefault(
            norm(label), Group(tag, label, name is not None, by_key.get(norm(label)))
        )
        present = {m.id for m in group.members}
        group.members.extend(
            m for m in mangas if work_of.get(m.id) in works and m.id not in present
        )

    created: list[str] = []
    added = 0
    if apply:
        for group in groups.values():
            if not group.members:
                continue
            if group.category is None:
                group.category = await service.client.create_category(group.name)
                created.append(group.name)
            todo = [m.id for m in group.members if group.category.id not in m.category_ids]
            await service.client.add_to_category(group.category.id, todo)
            added += len(todo)

    def describe(group: Group) -> dict[str, Any]:
        cat = group.category
        return {
            "tag": group.tag,
            "name": group.name,
            "known": group.known,
            "category_id": cat.id if cat else None,
            "exists": cat is not None and group.name not in created,
            "mangas": [
                {
                    "manga_id": m.id,
                    "title": m.title,
                    "work_id": work_of.get(m.id),
                    "assigned": cat is not None and (apply or cat.id in m.category_ids),
                }
                for m in group.members
            ],
        }

    unmapped = [{"manga_id": m.id, "title": m.title} for m in mangas if work_of.get(m.id) is None]
    return {
        "apply": apply,
        "categories": [describe(g) for g in groups.values()],
        "unmapped": unmapped,
        "created": created,
        "added": added,
    }
