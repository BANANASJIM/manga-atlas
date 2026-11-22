"""atlas.db: table definitions and the codes stored in them.

Other packages read `work`, `work_alias`, `person` and `work_person` (see the implementation plan);
the `label` table spells out every code so the file explains itself.
"""

from __future__ import annotations

from enum import IntEnum, IntFlag

SCHEMA_VERSION = 2


class Kind(IntEnum):
    """work.kind."""

    SERIES = 1
    VOLUME = 2  # a standalone volume with no series entry


class Status(IntEnum):
    """work.status."""

    UNKNOWN = 0
    ONGOING = 1
    COMPLETED = 2
    HIATUS = 3


STATUS_LABELS = {Status.ONGOING: "连载中", Status.COMPLETED: "已完结", Status.HIATUS: "停载"}


class Role(IntEnum):
    """work_person.role. Negative person ids are credits found only in the infobox text."""

    AUTHOR = 1
    ARTIST = 2
    ORIGINAL = 3
    SCRIPT = 4
    CHARACTER = 5
    ILLUSTRATOR = 6
    PUBLISHER = 7
    MAGAZINE = 8


ROLE_LABELS = {
    Role.AUTHOR: "作者",
    Role.ARTIST: "作画",
    Role.ORIGINAL: "原作",
    Role.SCRIPT: "脚本",
    Role.CHARACTER: "人物原案",
    Role.ILLUSTRATOR: "插图",
    Role.PUBLISHER: "出版社",
    Role.MAGAZINE: "连载杂志",
}
CREATOR_ROLES = frozenset(
    (Role.AUTHOR, Role.ARTIST, Role.ORIGINAL, Role.SCRIPT, Role.CHARACTER, Role.ILLUSTRATOR)
)
# Bangumi staff position codes for books (bangumi/common subject_staffs.yml).
ROLE_BY_POSITION = {
    2001: Role.AUTHOR,
    2002: Role.ARTIST,
    2007: Role.ORIGINAL,
    2010: Role.SCRIPT,
    2009: Role.CHARACTER,
    2003: Role.ILLUSTRATOR,
    2004: Role.PUBLISHER,
    2005: Role.MAGAZINE,
}
# Infobox fields that credit creators, for works whose staff Bangumi has not linked.
ROLE_BY_INFOBOX = {
    "作者": Role.AUTHOR,
    "作画": Role.ARTIST,
    "原作": Role.ORIGINAL,
    "原案": Role.ORIGINAL,
    "脚本": Role.SCRIPT,
    "人物原案": Role.CHARACTER,
    "插图": Role.ILLUSTRATOR,
}

# Bangumi relation codes (bangumi/common subject_relations.yml); codes do not collide across
# subject types except 改编 (1).
RELATION_LABELS = {
    1: "改编",
    2: "前传",
    3: "续集",
    4: "总集篇",
    5: "全集",
    6: "番外篇",
    7: "角色出演",
    8: "相同世界观",
    9: "不同世界观",
    10: "不同演绎",
    11: "衍生",
    12: "主线故事",
    14: "联动",
    99: "其他",
    1004: "画集",
    1005: "前传",
    1006: "续集",
    1007: "番外篇",
    1008: "主线故事",
    1010: "不同版本",
    1011: "角色出演",
    1012: "相同世界观",
    1013: "不同世界观",
    1014: "联动",
    1015: "不同演绎",
    1099: "其他",
    3001: "原声集",
    3002: "角色歌",
    3003: "片头曲",
    3004: "片尾曲",
    3005: "插入歌",
    3006: "印象曲",
    3007: "广播剧",
    3099: "其他",
    4002: "前传",
    4003: "续集",
    4006: "外传",
    4007: "角色出演",
    4008: "相同世界观",
    4009: "不同世界观",
    4010: "不同演绎",
    4012: "主线故事",
    4014: "联动",
    4015: "扩展包",
    4016: "不同版本",
    4017: "主版本",
    4018: "合集",
    4019: "收录作品",
    4099: "其他",
}
# Bangumi subject types, naming related subjects outside the catalogue.
SUBJECT_TYPE_LABELS = {1: "书籍", 2: "动画", 3: "音乐", 4: "游戏", 6: "三次元"}


class TagSource(IntFlag):
    """work_tag.src (bits) and work_page.src: where a work's tag comes from. Bangumi user tags
    count from tag_min_votes; the other sources are edited, so their tags count as they are."""

    BANGUMI = 1
    JAWIKI = 2  # Japanese Wikipedia categories
    YURIMIRU = 4  # ゆりみる, a yuri database's own tags

    @property
    def key(self) -> str:
        """The source's name in the API, its cache folder, and the facet group of its own tags."""
        return str(self.name).lower()


TAG_SOURCE_LABELS = {
    TagSource.BANGUMI: "Bangumi",
    TagSource.JAWIKI: "日文维基",
    TagSource.YURIMIRU: "ゆりみる",
}
EDITED = ~TagSource.BANGUMI  # the sources whose tags need no votes


def counted(votes: str, table: str = "") -> str:
    """SQL condition on a work_tag row (of `table`, when aliased): the work carries the tag.
    `votes` is the SQL for the least user votes that count, usually a parameter."""
    t = f"{table}." if table else ""
    return f"({t}official OR {t}votes >= {votes} OR {t}src & {int(EDITED)})"


DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE label(kind TEXT NOT NULL, code INTEGER NOT NULL, name TEXT NOT NULL,
  PRIMARY KEY(kind, code)) WITHOUT ROWID;
CREATE TABLE work(
  id INTEGER PRIMARY KEY,                 -- Bangumi subject id
  kind INTEGER NOT NULL,
  name TEXT NOT NULL,                     -- original title
  name_cn TEXT NOT NULL,                  -- Chinese title, '' when Bangumi has none
  title TEXT NOT NULL,                    -- name_cn or name
  date TEXT,                              -- YYYY-MM-DD
  year INTEGER,
  status INTEGER NOT NULL,
  region TEXT,
  region_inferred INTEGER NOT NULL,
  score REAL,
  votes INTEGER NOT NULL,
  rank INTEGER,
  rank_key INTEGER NOT NULL,              -- rank, or a large number when unranked
  collects INTEGER NOT NULL,              -- Bangumi collections: popularity
  vols INTEGER NOT NULL);
CREATE TABLE work_text(work_id INTEGER PRIMARY KEY, summary TEXT NOT NULL);
CREATE TABLE work_alias(work_id INTEGER NOT NULL, alias TEXT NOT NULL,
  PRIMARY KEY(work_id, alias)) WITHOUT ROWID;
-- Normalized text (NFKC, Simplified, casefold; one name per line) and its trigram index.
CREATE TABLE work_key(id INTEGER PRIMARY KEY, title TEXT NOT NULL, people TEXT NOT NULL);
CREATE VIRTUAL TABLE work_fts USING fts5(title, people, content='work_key', content_rowid='id',
  tokenize='trigram');
CREATE TABLE tag(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  grp TEXT NOT NULL);
CREATE TABLE tag_alias(key TEXT PRIMARY KEY, tag_id INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE work_tag(tag_id INTEGER NOT NULL, work_id INTEGER NOT NULL,
  votes INTEGER NOT NULL, official INTEGER NOT NULL,   -- Bangumi user votes, official tag
  src INTEGER NOT NULL,                                -- TagSource bits
  PRIMARY KEY(tag_id, work_id)) WITHOUT ROWID;
-- The work's page at a source other than Bangumi, which its tags from that source come from.
CREATE TABLE work_page(work_id INTEGER NOT NULL, src INTEGER NOT NULL, title TEXT NOT NULL,
  url TEXT NOT NULL, PRIMARY KEY(work_id, src)) WITHOUT ROWID;
CREATE TABLE person(id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_cn TEXT NOT NULL,
  kind INTEGER NOT NULL, works INTEGER NOT NULL);
CREATE TABLE person_alias(person_id INTEGER NOT NULL, alias TEXT NOT NULL,
  PRIMARY KEY(person_id, alias)) WITHOUT ROWID;
CREATE TABLE person_key(id INTEGER PRIMARY KEY, names TEXT NOT NULL);
CREATE VIRTUAL TABLE person_fts USING fts5(names, content='person_key', content_rowid='id',
  tokenize='trigram');
CREATE TABLE work_person(person_id INTEGER NOT NULL, work_id INTEGER NOT NULL,
  role INTEGER NOT NULL, PRIMARY KEY(person_id, work_id, role)) WITHOUT ROWID;
CREATE TABLE relation(work_id INTEGER NOT NULL, rel INTEGER NOT NULL, target_id INTEGER NOT NULL,
  PRIMARY KEY(work_id, rel, target_id)) WITHOUT ROWID;
CREATE TABLE target(id INTEGER PRIMARY KEY, type INTEGER NOT NULL, name TEXT NOT NULL,
  name_cn TEXT NOT NULL);
"""

INDEXES = """
CREATE INDEX work_by_rank ON work(rank_key, collects DESC);
CREATE INDEX work_by_heat ON work(collects DESC);
CREATE INDEX work_by_date ON work(date DESC);
CREATE INDEX work_tag_by_work ON work_tag(work_id, official, votes, src);
CREATE INDEX work_person_by_work ON work_person(work_id, role);
"""
