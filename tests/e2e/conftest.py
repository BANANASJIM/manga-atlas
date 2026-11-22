"""Browser tests: a fake backend serving the real static UI. Run with `pytest -m e2e tests/e2e`."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator

import pytest

from .fake_backend import Fake, LiveServer, create_app, load_catalog

if importlib.util.find_spec("playwright") is None:  # e2e dependency group not installed
    collect_ignore_glob = ["test_*.py"]


@pytest.fixture(scope="session")
def fake_server() -> Iterator[tuple[Fake, str]]:
    fake = Fake(load_catalog())
    fake.reset()
    with LiveServer(create_app(fake)) as url:
        yield fake, url


@pytest.fixture(scope="session")
def base_url(fake_server: tuple[Fake, str]) -> str:
    """Overrides pytest-base-url so page.goto('/') hits the fake backend."""
    return fake_server[1]


@pytest.fixture
def fake(fake_server: tuple[Fake, str]) -> Fake:
    fake_server[0].reset()
    return fake_server[0]
