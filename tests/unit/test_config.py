from dataclasses import fields
from pathlib import Path
from typing import get_origin, get_type_hints

import pytest

from manga_atlas.config import Settings, Source, load


def test_defaults_without_file(tmp_path: Path) -> None:
    assert load(tmp_path / "missing.toml") == Settings()


def test_overrides_and_rejects_unknown(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text('port = 5000\n[[sources]]\nid = "1"\nname = "x"\n')
    s = load(p)
    assert s.port == 5000 and s.sources[0].name == "x"
    p.write_text("nope = 1\n")
    with pytest.raises(ValueError):
        load(p)


def test_every_list_setting_becomes_a_tuple(tmp_path: Path) -> None:
    kinds = get_type_hints(Settings)
    lists = [f.name for f in fields(Settings) if get_origin(kinds[f.name]) is tuple]
    plain = [name for name in lists if name != "sources"]
    p = tmp_path / "c.toml"
    p.write_text(
        "".join(f'{name} = ["x"]\n' for name in plain)
        + 'data_dir = "~/atlas"\n[[sources]]\nid = "1"\nname = "s"\nlang = "ja"\n'
    )
    s = load(p)
    assert {name: getattr(s, name) for name in plain} == dict.fromkeys(plain, ("x",))
    assert s.sources == (Source("1", "s", lang="ja"),)
    assert s.data_dir == Path.home() / "atlas"
    hash(s)  # frozen and built from tuples: usable as a cache key


def test_a_list_setting_must_be_a_list(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text('r18_user_tags = "r18"\n')
    with pytest.raises(ValueError):
        load(p)


def test_the_example_config_loads_and_lists_the_default_sources() -> None:
    example = Path(__file__).parents[2] / "config.example.toml"
    assert load(example).sources == Settings().sources
