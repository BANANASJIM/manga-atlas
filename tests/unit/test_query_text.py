"""Text matching SQL: FTS5 phrases for 3+ characters, LIKE for shorter terms, match tiers."""

import sqlite3

from manga_atlas.query.search import WORK_TEXT, Sql, text_match


def run(terms: list[str]) -> list[tuple[int, int]]:
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE work_key(id INTEGER PRIMARY KEY, title TEXT, people TEXT);
        CREATE VIRTUAL TABLE work_fts USING fts5(title, people, content='work_key',
          content_rowid='id', tokenize='trigram');
        INSERT INTO work_key VALUES (1, '怪谈', ''), (2, '百合怪谈', ''),
          (3, '怪谈新耳袋' || char(10) || 'x', ''), (4, '别的', '怪谈作家'), (5, '100%_x', '');
        INSERT INTO work_fts(work_fts) VALUES ('rebuild');
        """
    )
    sql = Sql()
    source, conds, tier = text_match(sql, terms, WORK_TEXT, WORK_TEXT.columns)
    where = " AND ".join(conds) or "1"
    query = f"SELECT k.id, {tier} FROM {source} WHERE {where} ORDER BY k.id"
    return [(i, t) for i, t in con.execute(query, sql.args)]


def test_tiers_exact_then_prefix_then_anywhere() -> None:
    assert run(["怪谈"]) == [(1, 0), (2, 2), (3, 1), (4, 2)]
    assert run(["怪谈新耳袋"]) == [(3, 0)]


def test_like_patterns_are_escaped() -> None:
    assert run(["0%"]) == [(5, 2)]
    assert run(["%"]) == [(5, 2)]
    assert run(["_"]) == [(5, 2)]
