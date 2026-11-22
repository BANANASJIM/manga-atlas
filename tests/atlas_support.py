"""A small atlas.db with the real schema, for tests that need exact control over a few works."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from manga_atlas.build.schema import DDL, INDEXES, SCHEMA_VERSION, Role, TagSource
from manga_atlas.text import key

UNRANKED = 1 << 30


class Atlas:
    """Add rows, then `save()`: the file is then what `open_catalog` reads from `data_dir`."""

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(data_dir / "atlas.db")
        self.con.executescript(DDL + INDEXES)
        self.con.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))

    def work(
        self,
        id: int,
        summary: str | None = None,
        *,
        name: str | None = None,
        name_cn: str = "",
        title: str | None = None,
        collects: int = 0,
    ) -> None:
        self.con.execute(
            "INSERT INTO work(id, kind, name, name_cn, title, status, region_inferred, votes,"
            " rank_key, collects, vols) VALUES (?, 1, ?, ?, ?, 0, 0, 0, ?, ?, 0)",
            (id, name or f"原题{id}", name_cn, title or f"作品{id}", UNRANKED, collects),
        )
        if summary is not None:
            self.con.execute("INSERT INTO work_text VALUES (?, ?)", (id, summary))

    def title_alias(self, work: int, alias: str) -> None:
        self.con.execute("INSERT OR IGNORE INTO work_alias VALUES (?, ?)", (work, alias))

    def person(self, id: int, name: str) -> None:
        self.con.execute("INSERT INTO person VALUES (?, ?, '', 1, 0)", (id, name))

    def credit(self, work: int, person: int, role: Role) -> None:
        self.con.execute("INSERT INTO work_person VALUES (?, ?, ?)", (person, work, role))

    def tag(self, id: int, name: str, grp: str = "tag") -> None:
        self.con.execute("INSERT INTO tag VALUES (?, ?, ?, ?)", (id, key(name), name, grp))
        self.con.execute("INSERT INTO tag_alias VALUES (?, ?)", (key(name), id))

    def alias(self, spelling: str, tag: int) -> None:
        self.con.execute("INSERT INTO tag_alias VALUES (?, ?)", (key(spelling), tag))

    def tagged(
        self,
        work: int,
        tag: int,
        votes: int,
        official: bool = False,
        src: TagSource = TagSource.BANGUMI,
    ) -> None:
        self.con.execute(
            "INSERT INTO work_tag VALUES (?, ?, ?, ?, ?)", (tag, work, votes, official, src)
        )

    def save(self) -> None:
        self.con.commit()
        self.con.close()
