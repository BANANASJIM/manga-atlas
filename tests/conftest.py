from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from manga_atlas.build.tagsources import common
from manga_atlas.config import Settings, Source
from manga_atlas.web.app import create_app


def _offline(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("tests never reach the network", request=request)


@pytest.fixture(scope="session", autouse=True)
def no_tag_source_network() -> Iterator[None]:
    """A build in a test that would refresh a tag source fails at once instead, as offline."""
    refused = httpx.MockTransport(_offline)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(common, "client", lambda settings, **_: httpx.Client(transport=refused))
        yield


# Chinese sources (one listing authors, one disabled) ahead of the default Japanese ones.
SOURCES = (
    Source("zh-a", "中文源甲"),
    Source("zh-b", "中文源乙"),
    Source("zh-c", "中文源丙", lists_authors=True),
    Source("zh-off", "中文源丁", enabled=False),
    *Settings().sources,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, sources=SOURCES)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), base_url=f"http://{settings.host}:{settings.port}")
