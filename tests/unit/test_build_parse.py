"""Reading the dump: infobox fields, tag spelling folding, aliases, noise, script regions."""

from manga_atlas.build import infobox
from manga_atlas.build import vocab as vocab_rules

BOX = "\r\n".join(
    [
        "{{Infobox animanga/Manga",
        "|中文名= 进击的巨人",
        "|别名={",
        "[Attack on Titan]",
        "[第二中文名|巨人]",
        "[ ]",
        "}",
        "|作者= 諫山創",
        "|作画= ",
        "|版本:东立版={",
        "[版本名|進擊的巨人]",
        "[语言|繁体中文]",
        "}",
        "}}",
    ]
)


def test_infobox_fields_arrays_and_editions() -> None:
    box = infobox.parse(BOX)
    assert infobox.values(box, "中文名", "别名") == ["进击的巨人", "Attack on Titan", "巨人"]
    assert infobox.values(box, "作者") == ["諫山創"]
    assert infobox.values(box, "作画") == []  # empty values are dropped
    assert infobox.edition_names(box) == ["進擊的巨人"]
    assert infobox.parse("") == {}


def vocab() -> vocab_rules.Vocab:
    return vocab_rules.load()


def canon(raw: str, known: set[str] | None = None) -> str:
    v = vocab()
    return v.canonical(v.key(raw), known or set())


def test_decided_aliases_fold_and_horror_stays_apart() -> None:
    assert canon("GL") == canon("百合") == "百合"
    assert canon("爱情") == canon("恋爱") == "恋爱"
    assert canon("耽美") == canon("BL") == "bl"
    assert canon("惊悚") != canon("恐怖")


def test_traditional_and_width_variants_fold() -> None:
    assert canon("戀愛") == canon("恋爱")
    assert canon("ＢＬ") == "bl"


def test_suffix_folds_only_into_an_existing_tag() -> None:
    assert canon("恋爱漫画", {"恋爱"}) == "恋爱"
    assert canon("耽美漫画", set()) == "bl"  # via the alias table
    assert canon("怪奇漫画", set()) == "怪奇漫画"


def test_noise_and_attributes() -> None:
    v = vocab()
    for noise in ("漫画", "2015", "全3卷", "第一卷", "全一巻", "1990年代"):
        assert v.is_noise(v.key(noise)), noise
    assert not v.is_noise("百合")
    assert v.is_attribute("日本") and v.is_attribute(v.key("已完结"))
    assert not v.is_attribute("百合")


def test_script_region() -> None:
    v = vocab()
    assert v.script_region("やがて君になる") == "日本"
    assert v.script_region("도시정벌") == "韩国"
    assert v.script_region("哈利・波特") is None  # the middle dot is not kana
    assert v.script_region("进击的巨人") is None
