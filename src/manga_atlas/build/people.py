"""People: Bangumi's staff links, plus infobox credits Bangumi has not linked."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Callable, Container, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from manga_atlas.build import infobox
from manga_atlas.build.infobox import Infobox
from manga_atlas.build.schema import ROLE_BY_INFOBOX, ROLE_BY_POSITION, Role

Link = tuple[int, int, Role]  # person id, work id, role

_OPEN, _CLOSE = "(（[【", ")）]】"
_SEPARATORS = frozenset("、，,;；/／&＆×")
_JOINER = "・"  # joins names in a list, but also the parts of one foreign name
_ETC = ("等人", "等等", "等")
_BRACKETED = re.compile(r"[(（\[【][^)）\]】]*[)）\]】]")
_NICKNAME = "昵称"  # person alias kind too loose to identify anyone by
_STANDIN_BYTES = 6  # 48-bit ids: unique enough, and exact as JavaScript numbers


@dataclass(slots=True)
class Person:
    id: int  # negative for a name found only in infobox credits
    name: str
    name_cn: str
    kind: int  # Bangumi person type (1 individual, 2 company, 3 group); 0 when unknown
    aliases: list[str]

    def names(self) -> Iterator[str]:
        yield from (n for n in (self.name, self.name_cn, *self.aliases) if n)


def read_links(rows: Iterable[dict[str, Any]], works: Container[int]) -> set[Link]:
    links: set[Link] = set()
    for r in rows:
        if r["subject_id"] in works:
            role = ROLE_BY_POSITION.get(r["position"])
            if role is not None:
                links.add((r["person_id"], r["subject_id"], role))
    return links


def read_persons(rows: Iterable[dict[str, Any]], wanted: Container[int]) -> dict[int, Person]:
    persons: dict[int, Person] = {}
    for d in rows:
        if d["id"] not in wanted:
            continue
        box = infobox.parse(d["infobox"])
        cn = infobox.values(box, "简体中文名")
        aliases = [
            name
            for sub, value in box.get("别名", ())
            if sub != _NICKNAME
            for name in split_names(value)
        ]
        persons[d["id"]] = Person(d["id"], d["name"], cn[0] if cn else "", int(d["type"]), aliases)
    return persons


def split_names(text: str) -> list[str]:
    """Split a credit on list separators outside brackets: "A、B (C/D)" -> ["A", "B (C/D)"]."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text:
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(depth - 1, 0)
        if depth == 0 and ch in _SEPARATORS:
            parts.append("".join(buf))
            buf.clear()
        else:
            buf.append(ch)
    parts.append("".join(buf))
    names = []
    for part in parts:
        name = part.strip()
        for etc in _ETC:
            name = "" if name == etc else name.removesuffix(etc).strip()
        if name:
            names.append(name)
    return names


def unbracketed(name: str) -> str:
    return _BRACKETED.sub("", name).strip()


def standin_id(name_key: str) -> int:
    """Negative id for a name known only from infobox credits; the same in every build, so
    bookmarked searches by that name keep working."""
    digest = hashlib.blake2b(name_key.encode(), digest_size=_STANDIN_BYTES).digest()
    return -1 - int.from_bytes(digest, "big")


def add_infobox_credits(
    boxes: Mapping[int, Infobox],
    links: set[Link],
    persons: dict[int, Person],
    key: Callable[[str], str],
) -> int:
    """Credit infobox names that Bangumi has not linked: to the one known person with that name,
    else to a stand-in person (see standin_id) shared by every work crediting the same name.
    Returns the number of stand-ins created."""
    index: dict[str, set[int]] = defaultdict(set)
    for p in persons.values():
        for name in p.names():
            index[key(name)].add(p.id)
    credited: dict[int, set[str]] = defaultdict(set)  # work -> name keys already credited
    for pid, work, _ in links:
        credited[work].update(key(name) for name in persons[pid].names())
    standins: dict[str, int] = {}
    for work, box in boxes.items():
        seen = credited[work]
        for field, role in ROLE_BY_INFOBOX.items():
            for text in infobox.values(box, field):
                for name in split_names(text):
                    if _keys(name, key) & seen:
                        continue
                    pids = _resolve(name, index, key)
                    if not pids:
                        pid = standins.get(key(name), 0)
                        if not pid:
                            pid = standin_id(key(name))
                            while pid in persons:  # a hash collision, in theory
                                pid -= 1
                            standins[key(name)] = pid
                            persons[pid] = Person(pid, name, "", 0, [])
                        pids = [pid]
                    for pid in pids:
                        links.add((pid, work, role))
                        seen.update(_keys(name, key), (key(n) for n in persons[pid].names()))
    return len(standins)


def _keys(name: str, key: Callable[[str], str]) -> set[str]:
    return {key(name), key(unbracketed(name))} - {""}


def _resolve(name: str, index: Mapping[str, set[int]], key: Callable[[str], str]) -> list[int]:
    """The one known person called `name`; or, for "A・B", each part when all are known."""
    found = set().union(*(index.get(k, set()) for k in _keys(name, key)))
    if len(found) == 1:
        return list(found)
    parts = [p.strip() for p in name.split(_JOINER)]
    if len(parts) < 2:
        return []
    each = [set().union(*(index.get(k, set()) for k in _keys(p, key))) for p in parts]
    return [s.pop() for s in each] if all(len(s) == 1 for s in each) else []
