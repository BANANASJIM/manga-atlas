"""Bangumi wiki infobox text: `|key= value` lines and `|key={ [sub|value] ... }` arrays."""

from __future__ import annotations

Infobox = dict[str, list[tuple[str, str]]]  # key -> [(sub-key or "", value)]


def parse(text: str) -> Infobox:
    box: Infobox = {}
    key = ""
    in_array = False
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if in_array:
            if line.startswith("}"):
                in_array = False
            elif line.startswith("[") and line.endswith("]"):
                sub, sep, value = line[1:-1].partition("|")
                if not sep:
                    sub, value = "", sub
                if value.strip():
                    box.setdefault(key, []).append((sub.strip(), value.strip()))
            continue
        if line.startswith("|"):
            name, _, value = line[1:].partition("=")
            key, value = name.strip(), value.strip()
            if value == "{":
                in_array = True
            elif value:
                box.setdefault(key, []).append(("", value))
    return box


def values(box: Infobox, *keys: str) -> list[str]:
    return [value for key in keys for _, value in box.get(key, ())]


def edition_names(box: Infobox) -> list[str]:
    """Titles of the regional editions (`|版本:东立版={ [版本名|...] }`)."""
    return [
        value
        for key, items in box.items()
        if key.startswith("版本:")
        for sub, value in items
        if sub == "版本名"
    ]
