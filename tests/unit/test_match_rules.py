from dataclasses import replace

import pytest

from manga_atlas.config import Settings
from manga_atlas.match import Matcher, WorkKeys, WorkNames
from manga_atlas.match.matcher import part_no

M = Matcher(Settings())


def keys(
    name: str, name_cn: str = "", aliases: tuple[str, ...] = (), authors: tuple[str, ...] = ()
) -> WorkKeys:
    return M.work_keys(WorkNames(1, name, name_cn, aliases, authors))


def accepts(title: str, work_keys: WorkKeys) -> bool:
    return M.match_title(title, work_keys) is not None


@pytest.mark.parametrize(
    ("title", "name", "name_cn"),
    [
        ("寄生獸 完全版", "寄生獣", "寄生兽"),  # edition = same work
        ("航海王（全彩版）", "ONE PIECE", "航海王"),
        ("终将成为你（彩色条漫）", "やがて君になる", "终将成为你"),  # decoration in a bracket
        ("進擊的巨人", "進撃の巨人", "进击的巨人"),  # Japanese forms fold to Chinese
        ("海虎Ⅰ", "海虎", "海虎"),  # part one is the work itself
        ("ONE PIECE航海王", "ONE PIECE", "航海王"),  # 中文名+原名
        ("SPY×FAMILY カラー版", "SPY×FAMILY", "间谍过家家"),
        ("終將成為妳", "やがて君になる", "终将成为你"),  # 妳 / 你 variant
    ],
)
def test_same_work(title: str, name: str, name_cn: str) -> None:
    assert accepts(title, keys(name, name_cn))


@pytest.mark.parametrize(
    ("title", "name", "name_cn"),
    [
        ("航海王（番外篇）", "ONE PIECE", "航海王"),  # side story
        ("棋魂 插画集", "ヒカルの碁", "棋魂"),  # art book (no containment rule)
        ("SPY×FAMILY 公式ファンブック EYES ONLY", "SPY×FAMILY", "间谍过家家"),
        ("海虎Ⅱ", "海虎", "海虎"),  # part guard
        ("奇奇怪怪2", "奇奇怪怪", "奇奇怪怪"),
        ("海虎 (2)", "海虎", "海虎"),  # a bracketed part number is not decoration
        ("NEO寄生獸", "寄生獣", "寄生兽"),
        ("進擊的巨人Before the fall", "進撃の巨人", "进击的巨人"),
        ("钢之炼金术师外传", "鋼の錬金術師", "钢之炼金术师"),  # fuzzy, but a side story
    ],
)
def test_not_same_work(title: str, name: str, name_cn: str) -> None:
    assert not accepts(title, keys(name, name_cn))


def test_bracket_alternative_title() -> None:
    work = keys("ヴィンランド・サガ", "冰海战记", aliases=("文兰传奇",))
    assert accepts("海盜戰記 (冰海戰記)", work)


def test_chinese_half_of_mixed_alias() -> None:
    work = keys("ヴィンランド・サガ", "冰海战记", aliases=("海盜戰記VINLAND SAGA",))
    assert accepts("海盜戰記", work)


def test_mixed_alias_never_yields_a_franchise_prefix() -> None:
    spin_off = keys(
        "進撃の巨人 Before the fall",
        "进击的巨人外传 Before the fall 漫画版",
        aliases=("進撃の巨人 Before the fall", "Attack on Titan: Before the Fall"),
    )
    assert not accepts("進擊的巨人", spin_off)
    title_with_english = keys("海街diary", "海街日记", aliases=("海街diary",))
    assert not accepts("海街", title_with_english)


def test_fuzzy_threshold_comes_from_settings() -> None:
    work = M.work_keys(WorkNames(1, "鋼の錬金術師", "鋼之鍊金術師"))
    assert not accepts("鋼之鏈金術師", work)  # ratio 0.83 < 0.85
    loose = Matcher(replace(Settings(), match_fuzzy_threshold=0.8))
    assert loose.match_title("鋼之鏈金術師", loose.work_keys(WorkNames(1, "", "鋼之鍊金術師")))


def test_short_titles_are_flagged() -> None:
    match = M.match_title("乒乓", keys("ピンポン", "乒乓"))
    assert match is not None and M.is_short(match)
    long_match = M.match_title("進擊的巨人", keys("進撃の巨人", "进击的巨人"))
    assert long_match is not None and not M.is_short(long_match)


def test_part_numbers() -> None:
    assert part_no(M.key("海虎II")) == 2
    assert part_no(M.key("奇奇怪怪2")) == 2
    assert part_no(M.key("海虎")) == 1


@pytest.mark.parametrize(
    ("source_author", "authors", "expected"),
    [
        ("小畑健，堀田由美", ("小畑健", "ほったゆみ"), True),
        ("巖明均", ("岩明均",), True),  # traditional / simplified
        ("東清彥", ("あずまきよひこ", "东清彦"), True),  # Bangumi's Chinese name of the person
        ("宇佐崎 しろ, 西修", ("宇佐崎しろ",), True),  # spaced name
        ("Melord花爷", ("松本大洋",), False),
        (None, ("松本大洋",), None),
        ("松本大洋", (), None),
    ],
)
def test_author_match(
    source_author: str | None, authors: tuple[str, ...], expected: bool | None
) -> None:
    assert M.author_match(source_author, keys("x", authors=authors)) is expected


def test_queries_strip_editions_and_search_chinese_in_simplified() -> None:
    # All three Chinese sources answer Simplified (快乐人生 finds 快樂人生, 罪恶图书馆 finds
    # 罪惡圖書室, probe 2026-11-21); Japanese titles keep their script.
    work = WorkNames(1, "寄生獣 完全版", "寄生獸 完全版")
    assert M.queries(work, "zh") == ["寄生兽"]  # a Chinese title: only that, as before...
    assert M.queries(work, "zh", thorough=True) == ["寄生兽"]  # 寄生獣 folds to the same key
    monster = WorkNames(9, "MONSTER", "怪物")  # a source lists it as 怪物MONSTER, found by MONSTER
    assert M.queries(monster, "zh", thorough=True) == ["怪物", "MONSTER"]  # the work page only
    assert M.queries(work, "ja") == ["寄生獣"]
    assert M.queries(WorkNames(2, "ヒカルの碁", ""), "zh") == ["ヒカルの碁"]
    assert M.queries(WorkNames(3, "SPY×FAMILY", "间谍过家家"), "ja") == ["SPY×FAMILY"]


def test_without_a_chinese_title_chinese_sources_try_other_names() -> None:
    # Probe 2026-11-21: a Chinese source has these under the alias, not under the original title.
    work = WorkNames(4, "宇曽田みのりの代用料理", "", ("宇曾田实乃梨的平替料理",))
    assert M.queries(work, "zh") == ["宇曾田实乃梨的平替料理", "宇曽田みのりの代用料理"]
    assert M.queries(WorkNames(5, "スプライト", "", ("Sprite",)), "zh") == ["スプライト", "Sprite"]
    learned = WorkNames(
        6, "ぎるてぃらいぶらり", "", ("Guilty Library",), learned=(("罪惡圖書室", "k"),)
    )
    assert M.queries(learned, "zh") == ["罪恶图书室", "ぎるてぃらいぶらり", "Guilty Library"]
    # A source never searches the title it vouched for itself: it answers by the work's own names.
    assert M.queries(learned, "zh", source="k") == ["ぎるてぃらいぶらり", "Guilty Library"]
    # One search per matching key: script variants of one title are asked once.
    variants = WorkNames(7, "進撃の巨人", "", ("进击的巨人", "進擊的巨人", "进撃的巨人", "Attack"))
    assert M.queries(variants, "zh") == ["进击的巨人", "Attack"]
    many = WorkNames(8, "げんめい", "", ("别名一", "别名二", "别名三"))
    assert len(M.queries(many, "zh")) == Settings().match_queries_max


# ---- another translation, for an author to vouch for (Matcher.retitled)


def retitled(title: str, name: str, aliases: tuple[str, ...] = ()) -> bool:
    return M.retitled(title, keys(name, aliases=aliases), name) is not None


@pytest.mark.parametrize(
    ("title", "name"),
    [
        ("罪惡圖書室", "ぎるてぃらいぶらり"),  # probe 2026-11-21
        ("乒乓", "ピンポン"),
        ("棒球大聯盟2nd", "MAJOR 2nd"),  # the same part on both sides
    ],
)
def test_another_translation_can_be_vouched_for(title: str, name: str) -> None:
    assert retitled(title, name)


@pytest.mark.parametrize(
    ("title", "name"),
    [
        ("棒球大聯盟", "MAJOR 2nd"),  # the main series on a sequel's page (2nd is part two)
        ("棒球大聯盟2nd", "MAJOR"),  # a sequel on the main series' page
        ("JOJO的奇妙冒險 第五部 黃金之風", "ジョジョの奇妙な冒険 第6部 ストーンオーシャン"),
        ("罪惡圖書室 番外", "ぎるてぃらいぶらり"),  # a side story
        ("罪惡圖書室", "ぎるてぃらいぶらり 番外編"),  # the main series on a side story's page
        ("無職轉生~失意的魔術師篇", "無職転生~異世界行ったら本気だす~アンソロジー side:シルフィ"),
        ("夢幻紳士 夢幻外傳", "夢幻紳士 迷宮篇"),  # the series name in common
        ("Colorful Library", "Guilty Library"),  # an English word in common
    ],
)
def test_a_sibling_cannot_be_vouched_for(title: str, name: str) -> None:
    assert not retitled(title, name)


def test_a_chinese_alias_means_the_work_has_a_chinese_title() -> None:
    assert not M.chinese_titled(WorkNames(1, "ぎるてぃらいぶらり", "", ("Guilty Library",)))
    assert M.chinese_titled(WorkNames(2, "スプライト", "", ("精灵",)))
    assert M.chinese_titled(WorkNames(3, "ピンポン", "乒乓"))
    assert M.chinese_titled(WorkNames(4, "镖人", ""))  # a Chinese work: its own title
    assert M.queries(WorkNames(4, "镖人", "", ("Blades of the Guardians",)), "zh") == ["镖人"]
