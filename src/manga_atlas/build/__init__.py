"""Catalogue build: Bangumi Archive dump -> atlas.db."""

from __future__ import annotations

import argparse
import fcntl
import json
import sqlite3
import zipfile
from pathlib import Path

import httpx

from manga_atlas.build import archive, pipeline
from manga_atlas.build.schema import SCHEMA_VERSION
from manga_atlas.config import Settings


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dump", type=Path, help="build from this Archive zip, not the latest")
    parser.add_argument("--force", action="store_true", help="rebuild even if already up to date")


def built_from(settings: Settings) -> str | None:
    """dump_id recorded in the current atlas.db, if any and if written by this schema version."""
    if not settings.atlas_db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{settings.atlas_db}?mode=ro", uri=True)
        try:
            meta = dict(con.execute("SELECT key, value FROM meta"))
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return meta.get("dump_id") if meta.get("schema") == str(SCHEMA_VERSION) else None


def run(settings: Settings, dump: Path | None = None, force: bool = False) -> int:
    """Rebuild atlas.db from `dump`, or from the latest Archive dump (downloaded when new).
    One build at a time: another one (a manual run beside the weekly timer) exits."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with settings.build_lock.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pipeline.log(f"another build is running (it holds {settings.build_lock})")
            return 1
        return _run(settings, dump, force)


def _run(settings: Settings, dump: Path | None, force: bool) -> int:
    try:
        if dump is None:
            release = archive.latest_release(settings)
            if not force and built_from(settings) == release.dump_id:
                pipeline.log(f"atlas.db is up to date ({release.dump_id})")
                return 0
            pipeline.log(f"fetching {release.name} ({release.size / 1e6:.0f} MB)")
            dump = archive.fetch(release, settings)
            summary = pipeline.build(settings, dump)
            archive.prune(dump, settings)
        else:
            summary = pipeline.build(settings, dump)
    except (httpx.HTTPError, OSError, zipfile.BadZipFile, ValueError, sqlite3.Error) as e:
        pipeline.log(f"build failed: {e}")
        return 1
    pipeline.log(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0
