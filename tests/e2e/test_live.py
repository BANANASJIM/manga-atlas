"""Smoke flow against a real running MangaAtlas (host, port from config); skipped when it is down.

Run after integration: `uv run pytest -m live tests/e2e/test_live.py`. It opens one work, so it
checks the sources for that work once, then follows one read link, which may fetch that entry's
chapters.
"""

from __future__ import annotations

import re

import httpx
import pytest
from playwright.sync_api import Page, expect

from manga_atlas.config import load
from manga_atlas.suwayomi import manga_url

from .helpers import has_query

pytestmark = [pytest.mark.live]  # not e2e, so `-m e2e` never reaches the real services

READ_WITHIN_MS = 10_000  # acceptance: a ✓ or ≈ shows within 10 s


@pytest.fixture
def live_url() -> str:
    settings = load()
    url = f"http://{settings.host}:{settings.port}"
    try:
        httpx.get(f"{url}/api/status", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"no MangaAtlas server at {url}")
    return url


def test_live_smoke(page: Page, live_url: str) -> None:
    status = httpx.get(f"{live_url}/api/status", timeout=10).json()
    page.goto(f"{live_url}/")
    expect(page.locator(".results .summary b")).to_contain_text("共")

    page.keyboard.press("/")
    box = page.locator("#q")
    expect(box).to_be_focused()
    box.fill("#百合 #校园 -#恐怖")
    box.press("Enter")
    page.wait_for_url(has_query(t="百合,校园", x="恐怖"))
    expect(page.locator(".conds .cond.inc")).to_have_count(2)
    expect(page.locator(".conds .cond.exc")).to_have_count(1)
    first = page.locator(".grid a.card").first
    expect(first).to_be_visible()

    first.click()
    page.wait_for_url("**/work/*")
    expect(page.locator("h1.work-title")).not_to_be_empty()
    if not status.get("reachable"):
        expect(page.locator("#banner")).to_be_visible()
        return
    read = page.locator(".where li.src.yes a.read, .where li.src.maybe a.read").first
    expect(read).to_be_visible(timeout=READ_WITHIN_MS)
    href = read.get_attribute("href") or ""
    assert (found := re.fullmatch(r"/api/read/(\d+)", href)), href
    opened = page.request.get(live_url + href)  # the route sends us on to Suwayomi's own page
    assert opened.url == manga_url(str(status["suwayomi_url"]), int(found[1]))
    assert opened.status == 200
