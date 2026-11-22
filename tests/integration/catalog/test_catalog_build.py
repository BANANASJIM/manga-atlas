"""The fixture dump built into atlas.db, the latest-dump download, and the build command."""

import fcntl
import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from manga_atlas import build
from manga_atlas.build import archive, pipeline
from manga_atlas.build.people import standin_id
from manga_atlas.config import Settings

MakeSettings = Callable[[Path], Settings]


def rows(db: Path, sql: str) -> list[Any]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_works_selected_folded_and_adult_free(built: Path) -> None:
    assert [r[0] for r in rows(built, "SELECT id FROM work ORDER BY id")] == [
        101, 102, 103, 104, 108, 110, 111, 112, 204,
    ]  # fmt: skip
    meta = dict(rows(built, "SELECT key, value FROM meta"))
    assert meta["dump_id"] == "minidump" and meta["works"] == "9" and meta["built_at"]
    work = {r[0]: r for r in rows(built, "SELECT id, kind, date, year, status, vols FROM work")}
    assert work[103][2:4] == ("2008-07-04", 2008)  # date from its folded volume
    assert work[101][5] == 8 and work[204][1] == 2  # stated volumes; standalone volume
    assert work[112][3] == 1999  # year from the infobox start
    assert [work[w][4] for w in (101, 102, 103, 104, 108)] == [2, 2, 1, 0, 2]


def test_tag_vocabulary(built: Path) -> None:
    tags = {name: grp for name, grp in rows(built, "SELECT name, grp FROM tag")}
    assert tags == dict.fromkeys(("校园", "恋爱", "恐怖", "百合", "惊悚"), "genre")
    marks = rows(
        built,
        "SELECT t.name, wt.votes, wt.official FROM work_tag wt JOIN tag t ON t.id = wt.tag_id"
        " WHERE wt.work_id = 101 ORDER BY t.name",
    )
    assert marks == [("恋爱", 8, 0), ("校园", 12, 1), ("百合", 35, 1)]  # GL folded into 百合
    assert ("恋爱", 12, 0) in rows(
        built,
        "SELECT t.name, wt.votes, wt.official FROM work_tag wt JOIN tag t ON t.id = wt.tag_id"
        " WHERE wt.work_id = 102",
    )  # 戀愛 + 爱情 + 恋爱漫画
    aliases = dict(rows(built, "SELECT a.key, t.name FROM tag_alias a JOIN tag t ON t.id = tag_id"))
    assert aliases["gl"] == "百合" and aliases["爱情"] == "恋爱"


def test_regions_tagged_or_inferred(built: Path) -> None:
    found = rows(built, "SELECT id, region, region_inferred FROM work")
    got = {w: (r, bool(i)) for w, r, i in found}
    assert got[101] == ("日本", False) and got[102] == ("韩国", False)
    assert got[104] == ("日本", True) and got[110] == ("韩国", True)  # title script
    assert got[111] == ("日本", True)  # its publisher's other works
    assert got[112] == (None, False)


def test_cross_package_tables(built: Path) -> None:
    aliases = {a for (a,) in rows(built, "SELECT alias FROM work_alias WHERE work_id = 101")}
    assert aliases == {"やがて君になる", "终将成为你", "Bloom Into You", "成为你", "終將成為妳"}
    credits = set(
        rows(
            built,
            "SELECT p.name, wp.work_id, l.name FROM work_person wp JOIN person p"
            " ON p.id = wp.person_id JOIN label l ON l.kind = 'role' AND l.code = wp.role",
        )
    )
    assert ("仲谷鳰", 101, "作者") in credits and ("集英社", 103, "出版社") in credits
    assert ("浦沢直樹", 110, "作者") in credits  # infobox name of a known person
    assert {("甲山", 111, "原作"), ("乙川", 111, "原作")} <= credits
    standin = rows(built, "SELECT id, works FROM person WHERE name = '新人作家'")
    assert standin == [(standin_id("新人作家"), 2)]  # stable across builds
    assert not rows(built, "SELECT 1 FROM person WHERE id = 999")  # missing from the dump


def test_relations(built: Path) -> None:
    assert rows(built, "SELECT * FROM relation") == [
        (101, 1, 301), (101, 1006, 104), (103, 1, 301), (103, 1, 302), (104, 1005, 101),
        (110, 1099, 205),
    ]  # fmt: skip
    assert [r[0] for r in rows(built, "SELECT id FROM target")] == [205, 301, 302]


def test_rebuild_swaps_the_file_atomically(
    tmp_path: Path, dump: Path, make_settings: MakeSettings
) -> None:
    settings = make_settings(tmp_path)
    settings.atlas_db.write_bytes(b"old")
    assert build.run(settings, dump=dump) == 0
    assert rows(settings.atlas_db, "SELECT count(*) FROM work") == [(9,)]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["atlas.db", "build.lock"]
    assert build.built_from(settings) == "minidump"


def test_one_build_at_a_time(
    tmp_path: Path, dump: Path, make_settings: MakeSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = make_settings(tmp_path)
    settings.atlas_db.write_bytes(b"old")
    with settings.build_lock.open("a") as other:
        fcntl.flock(other, fcntl.LOCK_EX)  # the weekly timer's build, say
        assert build.run(settings, dump=dump) == 1
    assert "another build" in capsys.readouterr().err
    assert settings.atlas_db.read_bytes() == b"old"
    assert build.run(settings, dump=dump) == 0  # free again


def test_a_build_writes_only_its_own_staging_file(
    tmp_path: Path, dump: Path, make_settings: MakeSettings
) -> None:
    settings = make_settings(tmp_path)
    theirs = tmp_path / "atlas.db.new"  # what an older build may still be writing
    theirs.write_bytes(b"in progress")
    assert build.run(settings, dump=dump) == 0
    assert theirs.read_bytes() == b"in progress"
    assert rows(settings.atlas_db, "SELECT count(*) FROM work") == [(9,)]


def test_a_failed_write_leaves_no_staging_file(
    tmp_path: Path, dump: Path, make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def half_written(path: Path, _rows: object) -> None:
        path.write_bytes(b"half")
        raise OSError("disk full")

    monkeypatch.setattr(pipeline.writer, "write", half_written)
    settings = make_settings(tmp_path)
    assert build.run(settings, dump=dump) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["build.lock"]


def test_bad_dump_fails_cleanly(tmp_path: Path, make_settings: MakeSettings) -> None:
    settings = make_settings(tmp_path)
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    assert build.run(settings, dump=junk) == 1
    assert not settings.atlas_db.exists()


@pytest.fixture
def archive_server(monkeypatch: pytest.MonkeyPatch, dump: Path) -> list[str]:
    """Serve the latest-dump pointer and the fixture zip; record requested paths."""
    data = dump.read_bytes()
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("latest.json"):
            return httpx.Response(
                200,
                json={
                    "name": "dump-2026-11-17.210420Z.zip",
                    "browser_download_url": "https://example.test/dump.zip",
                    "size": len(data),
                    "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                },
            )
        return httpx.Response(200, content=data)

    def client(settings: Settings) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handle))

    monkeypatch.setattr(archive, "_client", client)
    return seen


def test_latest_dump_is_downloaded_once(
    tmp_path: Path, archive_server: list[str], make_settings: MakeSettings
) -> None:
    settings = make_settings(tmp_path)
    old = settings.dumps_dir / "dump-2020-01-01.000000Z.zip"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"stale")
    assert build.run(settings) == 0
    assert build.built_from(settings) == "dump-2026-11-17.210420Z"
    assert [p.name for p in settings.dumps_dir.iterdir()] == ["dump-2026-11-17.210420Z.zip"]
    assert len(archive_server) == 2
    assert build.run(settings) == 0  # same dump: nothing to do
    assert len(archive_server) == 3
    assert build.run(settings, force=True) == 0  # rebuilt from the kept download
    assert len(archive_server) == 4


def test_checksum_mismatch_is_rejected(
    tmp_path: Path, archive_server: list[str], make_settings: MakeSettings
) -> None:
    settings = make_settings(tmp_path)
    release = archive.Release("dump-x.zip", "https://example.test/dump.zip", 1, "0" * 64)
    with pytest.raises(ValueError, match="checksum"):
        archive.fetch(release, settings)
    assert not list(settings.dumps_dir.iterdir())


def test_build_summary_counts(tmp_path: Path, dump: Path, make_settings: MakeSettings) -> None:
    summary = pipeline.build(make_settings(tmp_path), dump)
    assert summary["works"] == 9 and summary["series"] == 12
    assert summary["adult_series"] == 4 and summary["adult_standalone"] == 1
    assert summary["tags_dropped_as_names"] == ["集英社"]
