"""Tags from other sources folded into the vocabulary beside Bangumi's, whatever the source."""

from manga_atlas.build import tags
from manga_atlas.build import vocab as vocab_rules
from manga_atlas.build.catalog import Subject, Tagger
from manga_atlas.build.schema import TagSource
from manga_atlas.config import Settings

SETTINGS = Settings(tag_vocab_min_works=2, source_tag_min_works=2)
BOTH = TagSource.BANGUMI | TagSource.JAWIKI


def subj(id: int, tags: list[tuple[str, int]], meta: list[str] | None = None) -> Subject:
    return Subject(
        id, True, False, f"s{id}", "", "", 0.0, 0, 0, 100, tags, meta or [], "", ""
    )  # fmt: skip


def vocabulary(sourced: dict[TagSource, dict[int, list[str]]]) -> tags.Vocabulary:
    works = {
        1: subj(1, [("恐怖", 5), ("友情", 1)], ["恐怖"]),
        2: subj(2, [("恐怖", 3), ("百合", 1)]),
        3: subj(3, [("百合", 4)]),
        4: subj(4, [("百合", 2)]),
    }
    tagger = Tagger.for_subjects(works.values(), vocab_rules.load(), SETTINGS)
    tallies = {w: tagger.tally(s) for w, s in works.items()}
    return tags.build(works, tallies, tagger, {}, {}, SETTINGS, sourced)


def rows(voc: tags.Vocabulary) -> dict[tuple[str, int], tuple[int, int, int]]:
    names = {t.id: t.name for t in voc.tags}
    return {(names[t], w): (votes, off, src) for t, w, votes, off, src in voc.work_tags}


def test_without_other_sources_every_row_is_bangumis() -> None:
    voc = vocabulary({})
    assert {src for *_, src in voc.work_tags} == {TagSource.BANGUMI}
    assert sorted(t.name for t in voc.tags) == ["恐怖", "百合"]


def test_a_source_tag_merges_into_the_same_tag_or_starts_one_of_its_own() -> None:
    voc = vocabulary(
        {
            TagSource.JAWIKI: {
                1: ["恐怖", "タイムループ"],
                2: ["GL", "タイムループ", "孤島（舞台）"],  # GL is 百合; 孤島: one work, too few
                9: ["タイムループ"],  # not in the catalogue
            }
        }
    )
    got = rows(voc)
    assert got[("恐怖", 1)] == (5, 1, BOTH)
    assert got[("百合", 2)] == (1, 0, BOTH)  # a weak user tag, now counted
    assert got[("百合", 3)] == (4, 0, TagSource.BANGUMI)
    assert got[("タイムループ", 1)] == (0, 0, TagSource.JAWIKI)
    assert ("タイムループ", 9) not in got and "孤島（舞台）" not in {t.name for t in voc.tags}
    groups = {t.name: t.group for t in voc.tags}
    assert groups["タイムループ"] == "jawiki" and groups["恐怖"] == "genre"
    names = {t.id: t.name for t in voc.tags}
    assert names[voc.aliases["gl"]] == "百合"
    assert names[voc.aliases["タイムループ"]] == "タイムループ"


def test_bangumi_votes_join_a_tag_only_another_source_brought() -> None:
    voc = vocabulary({TagSource.JAWIKI: {2: ["友情"], 3: ["友情"]}})
    got = rows(voc)
    assert got[("友情", 1)] == (1, 0, TagSource.BANGUMI)  # stored, though too weak to count
    assert got[("友情", 2)] == (0, 0, TagSource.JAWIKI)
    assert {t.name: t.group for t in voc.tags}["友情"] == "jawiki"


def test_tags_are_numbered_by_how_many_works_carry_them() -> None:
    voc = vocabulary({TagSource.JAWIKI: {w: ["タイムループ"] for w in (1, 2, 3, 4)}})
    assert [t.name for t in voc.tags] == ["タイムループ", "恐怖", "百合"]  # 2 and 2: by spelling


def test_a_tag_two_sources_give_is_grouped_with_the_one_giving_it_most() -> None:
    voc = vocabulary(
        {
            TagSource.JAWIKI: {1: ["前后辈"], 2: ["社会人"]},
            TagSource.YURIMIRU: {1: ["前后辈"], 3: ["前后辈"], 4: ["社会人"]},
        }
    )
    groups = {t.name: t.group for t in voc.tags}
    assert groups["前后辈"] == "yurimiru"  # on 3 works by ゆりみる, 1 by Wikipedia
    assert groups["社会人"] == "jawiki"  # 1 and 1: the first source
    got = rows(voc)
    assert got[("前后辈", 1)] == (0, 0, TagSource.JAWIKI | TagSource.YURIMIRU)
