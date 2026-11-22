"""Keyboard flow: / to search, Enter / Shift+Enter / Backspace for chips, arrows, Enter, R."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .fake_backend import SUWAYOMI_PATH, Fake, Params
from .helpers import has_query, total_text

pytestmark = pytest.mark.e2e


def first_yes(fake: Fake, work_id: int) -> int:
    """The entry the work's first ✓ source holds."""
    rows = fake.outcome(fake.catalog.works[work_id])
    return next(r["manga_id"] for r in rows if r["state"] == "yes")


def test_keyboard_flow(page: Page, fake: Fake, base_url: str) -> None:
    ids = [it["id"] for it in fake.search(Params.parse({"t": "百合"}))["items"]]
    suwayomi = base_url + SUWAYOMI_PATH
    page.goto("/")
    cards = page.locator(".grid a.card")
    expect(cards.first).to_be_visible()

    page.keyboard.press("/")
    box = page.locator("#q")
    expect(box).to_be_focused()
    page.keyboard.type("百合")
    expect(page.locator('#suggest-list .sg-tag.active[data-name="百合"]')).to_be_visible()
    page.keyboard.press("Enter")
    page.wait_for_url(has_query(t="百合"))
    expect(box).to_be_focused()

    page.keyboard.type("恐怖")
    page.keyboard.press("Shift+Enter")
    page.wait_for_url(has_query(t="百合", x="恐怖"))
    page.keyboard.press("Backspace")  # empty box: drop the last chip
    page.wait_for_url(has_query(t="百合", x=None))

    page.keyboard.press("ArrowDown")  # into the results
    expect(cards.nth(0)).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(cards.nth(1)).to_be_focused()
    page.keyboard.press("ArrowUp")  # first row: back to the box
    expect(box).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(cards.nth(1)).to_be_focused()

    page.keyboard.press("Enter")
    page.wait_for_url(f"**/work/{ids[1]}")
    expect(page.locator(".work-nav .pos")).to_have_text(f"第 2 / {len(ids)} 部")
    page.keyboard.press("ArrowRight")
    page.wait_for_url(f"**/work/{ids[2]}")
    page.keyboard.press("ArrowLeft")
    page.wait_for_url(f"**/work/{ids[1]}")

    manga_id = first_yes(fake, ids[1])
    read = page.locator(".where li.src.yes a.read").first
    expect(read).to_have_attribute("href", f"/api/read/{manga_id}")
    with page.context.expect_page() as tab:
        page.keyboard.press("r")
    expect(tab.value).to_have_url(f"{suwayomi}/manga/{manga_id}")

    page.go_back()  # back to the results with the last viewed work selected
    page.wait_for_url(has_query(t="百合"))
    expect(cards.nth(1)).to_be_focused()
    with page.context.expect_page() as tab:
        page.keyboard.press("r")  # R on a card reads the cached ✓ source directly
    expect(tab.value).to_have_url(f"{suwayomi}/manga/{manga_id}")
    assert fake.reads == [manga_id, manga_id]


def test_arrow_down_before_the_results_arrive_waits_for_them(page: Page, fake: Fake) -> None:
    fake.search_delay_s = 1.5
    page.goto("/")
    cards = page.locator(".grid a.card")
    page.keyboard.press("/")
    expect(page.locator("#q")).to_be_focused()
    assert cards.count() == 0  # the results are still on their way
    page.keyboard.press("ArrowDown")
    expect(cards.first).to_be_visible()
    expect(cards.nth(0)).to_be_focused()


def test_a_waiting_arrow_down_does_nothing_to_empty_results(page: Page, fake: Fake) -> None:
    fake.search_delay_s = 1.0
    page.goto("/?t=百合,恐怖,科幻")
    page.keyboard.press("/")
    page.keyboard.press("ArrowDown")
    expect(page.locator(".empty-title")).to_have_text("无结果")
    expect(page.locator("#q")).to_be_focused()


def test_typing_after_the_arrow_down_drops_it(page: Page, fake: Fake) -> None:
    fake.search_delay_s = 1.0
    page.goto("/")
    page.keyboard.press("/")
    page.keyboard.press("ArrowDown")
    page.keyboard.type("x")
    expect(page.locator(".grid a.card").first).to_be_visible()
    expect(page.locator("#q")).to_be_focused()  # the results did not take the focus


def test_leaving_the_page_drops_a_waiting_arrow_down(page: Page, fake: Fake) -> None:
    page.goto("/")
    expect(page.locator(".grid a.card").first).to_be_visible()
    fake.search_delay_s = 1.0
    page.locator('.facet[data-group="tag:genre"] .more').click()
    page.locator('.facets .chip[data-name="百合"]').click()
    page.wait_for_url(has_query(t="百合"))
    page.keyboard.press("/")
    page.keyboard.press("ArrowDown")  # wanted by the 百合 page, which is left before it can act
    page.go_back()
    page.go_forward()  # a new 百合 page: nobody pressed ↓ on it
    expect(page.locator(".summary b")).to_have_text(total_text(fake, t="百合"))
    expect(page.locator("#q")).to_be_focused()
