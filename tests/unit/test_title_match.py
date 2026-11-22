"""Matching works to a tag source's entries by Japanese title."""

from manga_atlas.build.tagsources.common import Work
from manga_atlas.build.tagsources.titles import match, settle, title_key

SUFFIXES = (" (漫画)", " (漫画作品)")


def work(id: int, name: str, *aliases: str) -> Work:
    return Work(id, name, aliases, 0)


def test_titles_compare_after_nfkc_casefold_and_spacing() -> None:
    assert title_key("ＢＬＡＭＥ！") == title_key("blame!")
    assert title_key("Steel_Ball  Run") == "steel ball run"


def test_a_disambiguated_title_comes_before_the_bare_one() -> None:
    titles = ["シカバネ", "シカバネ_(漫画)", "BLAME!"]
    got = match([work(1, "シカバネ"), work(2, "ＢＬＡＭＥ！")], titles, suffixes=SUFFIXES)
    assert got == {1: "シカバネ_(漫画)", 2: "BLAME!"}


def test_aliases_count_only_with_kana_and_after_the_original_title() -> None:
    titles = ["さよなら絵梨", "Goodbye", "寻找身体"]
    works = [work(1, "Sayonara Eri", "Goodbye", "さよなら絵梨"), work(2, "カラダ探し", "寻找身体")]
    assert match(works, titles, suffixes=SUFFIXES) == {1: "さよなら絵梨"}


def test_works_sharing_an_original_title_get_no_title_match() -> None:
    works = [work(1, "アオのハコ"), work(2, "アオのハコ"), work(3, "別作品")]
    assert match(works, ["アオのハコ", "別作品"]) == {3: "別作品"}


def test_a_shared_original_title_stays_out_of_reach_of_aliases() -> None:
    works = [work(1, "あお"), work(2, "あお"), work(3, "別の作品", "あお")]
    assert match(works, ["あお"]) == {}
    assert match(works, ["あお_(漫画)"], suffixes=SUFFIXES) == {}  # its suffixed form too


def test_a_linked_work_keeps_its_entry_and_its_namesake_gets_nothing() -> None:
    works = [work(1, "アオのハコ"), work(2, "アオのハコ")]
    assert match(works, ["アオのハコ"], linked={1: "アオのハコ"}) == {1: "アオのハコ"}


def test_a_linked_entry_is_taken() -> None:
    works = [work(1, "Gate"), work(2, "ゲート", "Gateの漫画")]
    titles = ["Gate", "Gateの漫画"]
    assert match(works, titles, linked={2: "Gate"}) == {2: "Gate"}  # 1 may not take it


def test_an_original_title_beats_an_alias_and_a_tie_goes_to_nobody() -> None:
    works = [
        work(1, "ドラゴン"),
        work(2, "ドラゴン外伝", "ドラゴン"),  # loses ドラゴン to 1's original title
        work(3, "竜A", "りゅう"),
        work(4, "竜B", "りゅう"),  # りゅう is claimed by aliases alike: nobody's
    ]
    assert match(works, ["ドラゴン", "りゅう"]) == {1: "ドラゴン"}


def test_a_lost_entry_falls_through_to_the_next() -> None:
    works = [work(1, "ドラゴン"), work(2, "ドラゴン外伝", "ドラゴン", "どらがい")]
    assert match(works, ["ドラゴン", "どらがい"]) == {1: "ドラゴン", 2: "どらがい"}


def test_an_article_reached_directly_beats_redirects_to_it() -> None:
    # (matched title, the article it leads to): the sequels' titles redirect to the original's.
    reached = {
        1: ("カラダ探し", "カラダ探し"),
        2: ("カラダ探し 解", "カラダ探し"),
        3: ("カラダ探し_異", "カラダ探し"),
        4: ("To_LOVEる", "To LOVEる"),  # the same title, spelled as the API normalizes it
        5: ("To LOVEる ダークネス", "To LOVEる"),
    }
    assert settle(reached, set()) == {1: "カラダ探し", 4: "To LOVEる"}


def test_redirects_alone_leave_an_article_to_nobody_unless_one_work_reaches_it() -> None:
    reached = {1: ("扉", "扉の物語"), 2: ("とびら", "扉の物語"), 3: ("屍鬼 新装版", "屍鬼")}
    assert settle(reached, set()) == {3: "屍鬼"}


def test_a_linked_article_stays_with_its_linked_work() -> None:
    reached = {
        1: ("Gate", "Gate (小説)"),  # Wikidata's link may itself be a redirect
        2: ("ゲートの話", "Gate (小説)"),
        3: ("Gate (小説)", "Gate (小説)"),  # even reached directly
        4: ("双子", "双子の話"),
        5: ("ふたご", "双子の話"),  # two linked works may share one article
    }
    assert settle(reached, {1, 4, 5}) == {1: "Gate (小説)", 4: "双子の話", 5: "双子の話"}
