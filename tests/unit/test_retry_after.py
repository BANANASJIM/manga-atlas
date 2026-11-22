"""How long a site asked us to wait: Retry-After as seconds or as an HTTP date."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from manga_atlas.build.tagsources.common import retry_after


def answer(value: str | None) -> httpx.Response:
    return httpx.Response(429, headers={} if value is None else {"Retry-After": value})


def test_seconds_or_a_date_or_the_default() -> None:
    later = format_datetime(datetime.now(UTC) + timedelta(seconds=120), usegmt=True)
    assert retry_after(answer("7"), 3) == 7.0
    assert retry_after(answer(later), 3) == pytest.approx(120, abs=2)
    assert retry_after(answer("Mon, 01 Jan 2001 00:00:00 GMT"), 3) == 0.0  # already past
    assert retry_after(answer("soon"), 3) == 3
    assert retry_after(answer(None), 3) == 3
