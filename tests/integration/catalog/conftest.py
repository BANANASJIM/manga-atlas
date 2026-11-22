"""A catalogue built once from tests/fixtures/minidump.zip; each test gets its own copy."""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.conftest import SOURCES

from manga_atlas.build import pipeline
from manga_atlas.config import Settings

DUMP = Path(__file__).parents[2] / "fixtures" / "minidump.zip"


def _settings(data_dir: Path) -> Settings:
    # The fixture is tiny: let tags and publishers count from two works.
    return Settings(
        data_dir=data_dir, sources=SOURCES, tag_vocab_min_works=2, region_entity_min_works=2
    )


@pytest.fixture(scope="session")
def dump() -> Path:
    return DUMP


@pytest.fixture(scope="session")
def make_settings() -> Callable[[Path], Settings]:
    return _settings


@pytest.fixture(scope="session")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    settings = _settings(tmp_path_factory.mktemp("catalog"))
    pipeline.build(settings, DUMP)
    return settings.atlas_db


@pytest.fixture
def settings(tmp_path: Path, built: Path) -> Settings:
    shutil.copy(built, tmp_path / "atlas.db")
    return _settings(tmp_path)
