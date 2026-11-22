"""Work page credits: which of them make a creator."""

from pathlib import Path

from tests.atlas_support import Atlas

from manga_atlas.build.schema import CREATOR_ROLES, ROLE_LABELS, Role
from manga_atlas.config import Settings
from manga_atlas.query.db import open_catalog
from manga_atlas.query.work import work


def test_only_creator_roles_are_flagged(tmp_path: Path) -> None:
    atlas = Atlas(tmp_path)
    atlas.work(1)
    for role in Role:  # one person per role
        atlas.person(int(role), f"人{role.name}")
        atlas.credit(1, int(role), role)
    atlas.save()
    settings = Settings(data_dir=tmp_path)

    detail = work(open_catalog(settings), settings, 1)

    assert detail is not None
    flags = {p["role"]: p["creator"] for p in detail["people"]}
    assert flags == {ROLE_LABELS[r]: r in CREATOR_ROLES for r in Role}
    assert flags["作者"] is True
    assert flags["出版社"] is False and flags["连载杂志"] is False
