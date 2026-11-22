"""Screenshots for review, against the fake backend.

ATLAS_SHOTS=<dir> uv run pytest -m e2e tests/e2e/test_shots.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from .fake_backend import Fake

SHOTS = os.environ.get("ATLAS_SHOTS")
VIEWPORT = {"width": 1440, "height": 900}

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not SHOTS, reason="set ATLAS_SHOTS=<dir>")]


def settle(page: Page) -> None:
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready.then(() => true)")


def test_screenshots(page: Page, fake: Fake) -> None:
    out = Path(SHOTS or ".")
    out.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size(VIEWPORT)

    page.goto("/?t=百合,校园&x=恐怖")
    expect(page.locator(".conds .cond")).to_have_count(3)
    settle(page)
    page.screenshot(path=out / "browse-chips.png")

    page.goto("/work/101")
    expect(page.locator(".where li.src.checking")).to_have_count(0)
    settle(page)
    page.screenshot(path=out / "work.png")

    page.emulate_media(color_scheme="dark")
    page.goto("/?t=百合")
    expect(page.locator(".tag-head")).to_be_visible()
    settle(page)
    page.screenshot(path=out / "dark.png")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_organize_screenshots(page: Page, fake: Fake, scheme: str) -> None:
    out = Path(SHOTS or ".")
    out.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size(VIEWPORT)
    page.emulate_media(color_scheme=scheme)

    page.goto("/?t=百合,校园&x=恐怖")
    button = page.locator(".conds button.organize")
    expect(button).to_be_visible()
    settle(page)
    page.screenshot(path=out / f"organize-button-{scheme}.png")

    button.click()
    dialog = page.locator("dialog.org-dialog")
    expect(dialog.locator(".org-group")).to_have_count(2)
    settle(page)
    page.screenshot(path=out / f"organize-preview-{scheme}.png")

    dialog.get_by_role("button", name="应用").click()
    expect(dialog.locator(".org-body a.ext-link")).to_have_count(2)
    settle(page)
    page.screenshot(path=out / f"organize-result-{scheme}.png")
