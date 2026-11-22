"""Write minidump.zip: a tiny Bangumi Archive dump exercising every catalogue rule.

Regenerate after editing: `uv run python tests/fixtures/make_minidump.py`
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("minidump.zip")
STAMP = (2026, 11, 17, 21, 4, 20)  # fixed, so the zip is byte-for-byte reproducible


def box(*lines: str) -> str:
    return "\r\n".join(["{{Infobox animanga/Manga", *lines, "}}"])


def subject(
    id: int,
    name: str,
    name_cn: str = "",
    *,
    type: int = 1,
    platform: int = 1001,
    series: bool = True,
    nsfw: bool = False,
    tags: dict[str, int] | None = None,
    meta: list[str] | None = None,
    infobox: str = "",
    collects: int = 100,
    score: float = 0,
    votes: int = 0,
    rank: int = 0,
    date: str = "",
    summary: str = "",
) -> dict[str, Any]:
    return {
        "id": id,
        "type": type,
        "name": name,
        "name_cn": name_cn,
        "infobox": infobox or box(),
        "platform": platform,
        "summary": summary,
        "nsfw": nsfw,
        "tags": [{"name": k, "count": v} for k, v in (tags or {}).items()],
        "meta_tags": meta or [],
        "score": score,
        "score_details": {"8": votes} if votes else {},
        "rank": rank,
        "date": date,
        "favorite": {"wish": collects // 2, "done": collects - collects // 2, "doing": 0,
                     "on_hold": 0, "dropped": 0},
        "series": series,
    }  # fmt: skip


SUBJECTS = [
    subject(
        101,
        "やがて君になる",
        "终将成为你",
        tags={"百合": 30, "GL": 5, "校园": 12, "恋爱": 8, "漫画": 50, "2015": 3, "集英社": 5},
        meta=["漫画", "日本", "已完结", "百合", "校园"],
        infobox=box(
            "|中文名= 终将成为你",
            "|别名={",
            "[Bloom Into You]",
            "[第二中文名|成为你]",
            "}",
            "|作者= 仲谷鳰",
            "|册数= 8",
            "|版本:台湾角川版={",
            "[版本名|終將成為妳]",
            "[语言|繁体中文]",
            "}",
        ),
        collects=5000,
        score=8.6,
        votes=50,
        rank=74,
        date="2015-04-27",
        summary="  两个女孩。 ",
    ),
    subject(
        102,
        "사랑 이야기",
        "恋爱物语",
        tags={"戀愛": 6, "爱情": 3, "恋爱漫画": 3, "校园": 4, "韩漫": 5, "全3卷": 2},
        infobox=box("|作者= 乙川", "|结束= 2020年5月"),
        collects=100,
        score=6.0,
        votes=4,
        date="2018-01-01",
    ),
    subject(
        103,
        "屍鬼",
        "尸鬼 漫画版",
        tags={"恐怖": 10, "悬疑": 3, "连载中": 3, "集英社": 4},
        meta=["日本", "惊悚"],
        collects=300,
        score=7.6,
        votes=25,
        rank=2047,
    ),
    subject(
        104,
        "百合と怪談",
        "百合怪谈",
        tags={"百合": 5, "恐怖": 4, "校园": 3},
        infobox=box("|作者= 甲山"),
        collects=800,
        score=9.5,
        votes=3,
        date="2019-06-01",
    ),
    subject(105, "Adult A", nsfw=True, tags={"百合": 9}, collects=900),
    subject(106, "Adult B", meta=["R18"], collects=900),
    subject(107, "Adult C", tags={"R18": 3}, collects=900),
    subject(
        108,
        "ほぼ普通",
        "几乎普通",
        tags={"R18": 1, "完结": 2},
        collects=60,
        date="2010-02-03",
    ),
    subject(109, "Adult D", tags={"成年コミック": 2}, collects=900),
    subject(
        110,
        "도시정벌",
        "都市征伐",
        collects=50,
        date="2005-01-01",
        infobox=box("|作者= 浦沢直樹、新人作家"),
    ),
    subject(111, "某漫画物语", collects=20, infobox=box("|原作= 甲山・乙川"), tags={"集英社": 3}),
    subject(112, "无名之作", collects=5, infobox=box("|作画= 新人作家", "|开始= 1999年3月号")),
    # Volumes.
    subject(201, "やがて君になる 1", series=False, date="2015-01-01", collects=40),
    subject(202, "屍鬼 1", series=False, date="2008-07-04", collects=30),
    subject(203, "やがて君になる 2", series=False, collects=30),
    subject(
        204,
        "読切作品集",
        "读切作品集",
        series=False,
        collects=15,
        tags={"短篇集": 3},
        date="2012-12-12",
    ),
    subject(205, "無名の一冊", series=False, collects=3),
    subject(206, "Adult volume", series=False, nsfw=True, collects=50),
    subject(207, "Adult A 1", series=False, collects=20),
    # Not manga.
    subject(301, "やがて君になる", "终将成为你", type=2, platform=1, collects=9000),
    subject(302, "屍鬼", type=1, platform=1002, collects=500),
    subject(303, "Adult anime", type=2, platform=1, nsfw=True),
]

RELATIONS = [
    (201, 1002, 101), (203, 1002, 101), (203, 1002, 104), (103, 1003, 202), (105, 1003, 207),
    (101, 1, 301), (101, 1, 303), (103, 1, 302), (202, 1, 301), (101, 1006, 104),
    (104, 1005, 101), (110, 1099, 205), (108, 1006, 105), (999999, 1, 101),
]  # fmt: skip

CREDITS = [
    (1, 101, 2001), (2, 103, 2002), (3, 103, 2007), (50, 101, 2004), (50, 103, 2004),
    (50, 111, 2004), (51, 101, 2005), (60, 108, 2001), (61, 104, 2001), (62, 102, 2001),
    (2, 205, 2001), (999, 101, 2001), (1, 101, 2099),
]  # fmt: skip


def person(id: int, name: str, type: int = 1, cn: str = "", aliases: str = "") -> dict[str, Any]:
    lines = ["{{Infobox Crt", f"|简体中文名= {cn}", "|别名={", *aliases.split(";"), "}", "}}"]
    return {"id": id, "name": name, "type": type, "career": [], "summary": "",
            "infobox": "\r\n".join(line for line in lines if line)}  # fmt: skip


PERSONS = [
    person(1, "仲谷鳰", cn="仲谷鸟", aliases="[罗马字|Nakatani Nio];[昵称|甲山]"),
    person(2, "藤崎竜", cn="藤崎龙"),
    person(3, "小野不由美"),
    person(50, "集英社", type=2),
    person(51, "コミック電撃大王", type=2),
    person(60, "浦沢直樹", cn="浦泽直树"),
    person(61, "甲山"),
    person(62, "乙川"),
    person(70, "Nobody"),
]


def jsonl(rows: list[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode()


def main() -> None:
    members = {
        "subject.jsonlines": jsonl(SUBJECTS),
        "subject-relations.jsonlines": jsonl(
            [{"subject_id": a, "relation_type": r, "related_subject_id": b, "order": 0}
             for a, r, b in RELATIONS]
        ),
        "subject-persons.jsonlines": jsonl(
            [{"person_id": p, "subject_id": s, "position": pos, "appear_eps": ""}
             for p, s, pos in CREDITS]
        ),
        "person.jsonlines": jsonl(PERSONS),
    }  # fmt: skip
    with zipfile.ZipFile(OUT, "w") as zf:
        for name, data in members.items():
            zf.writestr(zipfile.ZipInfo(name, STAMP), data, compress_type=zipfile.ZIP_DEFLATED)


if __name__ == "__main__":
    main()
