from manga_atlas.text import key, norm, simplified


def test_norm_folds_traditional_width_and_case() -> None:
    assert norm("殺手阿一") == norm("杀手阿一")
    assert norm("ＣＩＴＲＵＳ") == "citrus"


def test_key_is_norm_with_single_spaces() -> None:
    assert key("  ＧＬ \u3000 戀愛 ") == "gl 恋爱"


def test_simplified_keeps_the_case() -> None:
    assert simplified("Ｒ１８ 戀愛") == "R18 恋爱"
