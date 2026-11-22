"""Bangumi Archive: find the latest dump, download it, stream its JSON-lines files."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from manga_atlas.config import Settings

SUBJECTS = "subject.jsonlines"
RELATIONS = "subject-relations.jsonlines"
CREDITS = "subject-persons.jsonlines"
PERSONS = "person.jsonlines"
_CHUNK = 1 << 20


@dataclass(frozen=True)
class Release:
    name: str  # e.g. dump-2026-11-17.210420Z.zip
    url: str
    size: int
    sha256: str  # hex digest; empty when the pointer carries none

    @property
    def dump_id(self) -> str:
        return dump_id(Path(self.name))


def dump_id(path: Path) -> str:
    return path.name.removesuffix(".zip")


def _client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": settings.user_agent},
        timeout=settings.download_timeout_s,
        follow_redirects=True,
    )


def latest_release(settings: Settings) -> Release:
    """Read the repo's pointer to its newest weekly dump."""
    url = settings.archive_latest_url.format(repo=settings.archive_repo)
    with _client(settings) as client:
        response = client.get(url)
        response.raise_for_status()
        info = response.json()
    return Release(
        name=str(info["name"]),
        url=str(info["browser_download_url"]),
        size=int(info["size"]),
        sha256=str(info.get("digest") or "").removeprefix("sha256:"),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(release: Release, settings: Settings) -> Path:
    """Download the dump into dumps_dir, or reuse a complete copy already there."""
    settings.dumps_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.dumps_dir / release.name
    if (
        dest.exists()
        and dest.stat().st_size == release.size
        and (not release.sha256 or _sha256(dest) == release.sha256)
    ):
        return dest
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    with _client(settings) as client, client.stream("GET", release.url) as response:
        response.raise_for_status()
        with part.open("wb") as fh:
            for chunk in response.iter_bytes(_CHUNK):
                fh.write(chunk)
                digest.update(chunk)
    if release.sha256 and digest.hexdigest() != release.sha256:
        part.unlink()
        raise ValueError(f"checksum mismatch for {release.name}")
    part.replace(dest)
    return dest


def prune(keep: Path, settings: Settings) -> None:
    """Delete other downloads; each dump is about 450 MB."""
    for path in settings.dumps_dir.glob("dump-*.zip*"):
        if path != keep:
            path.unlink()


def rows(archive: zipfile.ZipFile, member: str) -> Iterator[dict[str, Any]]:
    """Stream one JSON-lines member without extracting it."""
    with archive.open(member) as fh:
        for line in fh:
            if line.strip():
                row: dict[str, Any] = json.loads(line)
                yield row
