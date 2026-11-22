from pathlib import Path

import pytest

from manga_atlas import build, cli
from manga_atlas.config import Settings


def test_build_passes_its_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_run(settings: Settings, dump: Path | None = None, force: bool = False) -> int:
        seen.update(dump=dump, force=force)
        return 0

    monkeypatch.setattr(build, "run", fake_run)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["build", "--dump", "x.zip", "--force"]) == 0
    assert seen == {"dump": Path("x.zip"), "force": True}
    assert cli.main(["build"]) == 0
    assert seen == {"dump": None, "force": False}
