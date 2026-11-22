"""Credits from infobox text, and regions from tags, publishers or the title's script."""

from manga_atlas.build import infobox, region
from manga_atlas.build import vocab as vocab_rules
from manga_atlas.build.catalog import Tally
from manga_atlas.build.people import Person, add_infobox_credits, split_names, standin_id
from manga_atlas.build.schema import Role
from manga_atlas.config import Settings


def vocab() -> vocab_rules.Vocab:
    return vocab_rules.load()


def test_split_names() -> None:
    assert split_names("A、B (C/D)") == ["A", "B (C/D)"]
    assert split_names("拓人，等") == ["拓人"]
    assert split_names("甲山等 / 乙川") == ["甲山", "乙川"]
    assert split_names("  ") == []


def test_infobox_credits_link_known_names_or_share_a_standin() -> None:
    v = vocab()
    persons = {
        1: Person(1, "浦沢直樹", "浦泽直树", 1, []),
        2: Person(2, "同名", "", 1, []),
        3: Person(3, "同名", "", 1, []),
        4: Person(4, "甲山", "", 1, []),
        5: Person(5, "乙川", "", 1, []),
    }
    links = {(1, 10, Role.AUTHOR)}
    boxes = {
        10: infobox.parse("|作者= 浦泽直树"),  # already linked: skipped
        11: infobox.parse("|作者= 浦沢直樹、新人"),
        12: infobox.parse("|作画= 新人\n|原作= 同名"),
        13: infobox.parse("|原作= 甲山・乙川"),
    }
    created = add_infobox_credits(boxes, links, persons, v.key)
    assert created == 2  # 新人, and the ambiguous 同名
    by_work: dict[int, set[tuple[str, Role]]] = {}
    for pid, work, role in links:
        by_work.setdefault(work, set()).add((persons[pid].name, role))
    assert by_work[10] == {("浦沢直樹", Role.AUTHOR)}
    assert by_work[11] == {("浦沢直樹", Role.AUTHOR), ("新人", Role.AUTHOR)}
    assert by_work[12] == {("新人", Role.ARTIST), ("同名", Role.ORIGINAL)}
    assert by_work[13] == {("甲山", Role.ORIGINAL), ("乙川", Role.ORIGINAL)}
    standin = next(pid for pid, p in persons.items() if p.name == "新人")
    assert standin == standin_id(v.key("新人")) < 0
    assert {w for pid, w, _ in links if pid == standin} == {11, 12}


def test_region_from_tags_publishers_then_script() -> None:
    v = vocab()
    settings = Settings(region_entity_min_works=2, region_entity_min_share=0.9)
    tallies = {
        1: Tally({"日本": 1}, ["日本"]),  # official
        2: Tally({"韩国": 5}, []),  # user tag with enough votes
        3: Tally({"中国": 1}, []),  # too few votes: falls through to the publisher
        4: Tally({}, []),
        5: Tally({}, []),
        6: Tally({}, []),
    }
    pub = 900
    links = [(pub, 1, Role.PUBLISHER), (pub, 2, Role.PUBLISHER), (pub, 3, Role.MAGAZINE)]
    links += [(901, w, Role.PUBLISHER) for w in (1, 2, 4)]
    titles = {1: "x", 2: "x", 3: "某作", 4: "某作", 5: "ふつう", 6: "无名"}
    got = region.assign(tallies, links, titles, v, settings)
    assert got[1] == ("日本", False) and got[2] == ("韩国", False)
    # publisher 900 saw 日本 and 韩国 once each: below the agreement share, so no prediction
    assert got[3] == (None, False)
    assert got[4] == (None, False)
    assert got[5] == ("日本", True)  # kana in the title
    assert got[6] == (None, False)
    settings = Settings(region_entity_min_works=1, region_entity_min_share=0.5)
    assert region.assign(tallies, links, titles, v, settings)[3] == ("日本", True)
