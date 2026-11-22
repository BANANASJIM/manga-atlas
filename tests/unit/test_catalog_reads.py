import sqlite3
from pathlib import Path

import pytest
from tests.suwayomi_support import Work, make_atlas

from manga_atlas.config import Settings
from manga_atlas.match import Matcher, catalog


@pytest.fixture
def con(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "atlas.db"
    titan = Work(1, "進撃の巨人", "进击的巨人", authors=("諫山創",), publishers=("講談社",))
    titan.collects, titan.tags = 900, {"恐怖": 0, "战斗": 5}  # 0 votes: an official tag
    one_piece = Work(2, "ONE PIECE", "航海王", aliases=("海賊王",), collects=950, tags={"恐怖": 1})
    vinland = Work(3, "ヴィンランド・サガ", "冰海战记", aliases=("海盜戰記VINLAND SAGA",))
    make_atlas(path, [titan, one_piece, vinland])
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO tag_alias VALUES ('horror', 1)")
    return connection


def test_work_names_and_creator_roles(con: sqlite3.Connection) -> None:
    names = catalog.work_names(con, [1, 2, 404])
    assert set(names) == {1, 2}
    assert names[1].authors == ("諫山創",)  # the publisher is not an author
    assert names[2].aliases == ("海賊王",)  # name and name_cn are not repeated as aliases


def test_tags_count_official_or_enough_votes(con: sqlite3.Connection) -> None:
    assert catalog.tag_work_ids(con, "恐怖", 2) == ("恐怖", {1})  # official on 1; 1 vote on 2
    assert catalog.tag_work_ids(con, " HORROR ", 2) == ("恐怖", {1})  # via tag_alias
    assert catalog.tag_work_ids(con, "战斗", 2) == ("战斗", {1})
    assert catalog.tag_work_ids(con, "不存在", 2) is None


def test_popularity_and_title_index(con: sqlite3.Connection) -> None:
    assert catalog.popular_work_ids(con, 2) == [2, 1]
    assert catalog.popularity(con, [1, 3]) == {1: 900, 3: 0}
    m = Matcher(Settings())
    index = catalog.title_index(con, m)
    assert index[m.key("進擊的巨人")] == [1]
    assert index[m.key("ONE PIECE航海王")] == [2]  # name_cn + name
    assert index[m.key("海賊王")] == [2]
    assert index[m.key("海盜戰記")] == [3]  # Chinese half of a mixed alias


def test_missing_catalogue(tmp_path: Path) -> None:
    with pytest.raises(catalog.CatalogMissing), catalog.connect(Settings(data_dir=tmp_path)):
        pass
