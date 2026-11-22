"""整理书架: the button, the preview, an apply sent only on the click, the result, errors, Esc."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .fake_backend import SUWAYOMI_PATH, Fake

pytestmark = pytest.mark.e2e

BUTTON = ".conds button.organize"
DIALOG = "dialog.org-dialog"
WAIT_MS = 500  # long enough for a request that should not come


def applies(fake: Fake) -> list[bool]:
    return [bool(body.get("apply")) for body in fake.organizes]


def test_the_button_needs_an_included_tag_and_suwayomi(page: Page, fake: Fake) -> None:
    for url in ("/?x=恐怖", "/?a=1"):  # conditions, but no included tag
        page.goto(url)
        expect(page.locator(".conds .cond")).to_have_count(1)
        expect(page.locator(BUTTON)).to_have_count(0)
    page.goto("/?t=百合&x=恐怖")
    button = page.locator(BUTTON)
    expect(button).to_have_text("整理书架")
    expect(button).to_be_enabled()
    assert fake.organizes == []  # showing it asks nothing

    fake.suwayomi_up = False
    page.goto("/?t=百合")
    expect(page.locator("#banner")).to_be_visible()
    expect(button).to_be_disabled()
    expect(button).to_have_attribute("title", "Suwayomi 连不上")


def test_the_preview_shows_each_category_and_what_it_gains(page: Page, fake: Fake) -> None:
    preview = fake.organize(["百合", "校园"], apply=False)
    yuri, school = preview["categories"]
    assert yuri["category_id"] is None and school["category_id"] is not None  # new, existing
    assert [m["assigned"] for m in school["mangas"]].count(True) == 1
    assert len(preview["unmapped"]) == 1

    page.goto("/?t=百合,校园&x=恐怖")
    page.locator(BUTTON).click()
    dialog = page.locator(DIALOG)
    expect(dialog.locator("h2")).to_have_text("整理书架")
    groups = dialog.locator(".org-group")
    expect(groups).to_have_count(2)
    assert fake.organizes == [{"tags": ["百合", "校园"], "apply": False}]  # included tags only

    first, second = groups.nth(0), groups.nth(1)
    expect(first.locator("h3")).to_have_text("百合")
    expect(first.locator(".org-state")).to_have_text("新建分类")
    expect(first.locator(".org-count")).to_have_text(f"加入 {len(yuri['mangas'])} 部")
    expect(first.locator(".org-item")).to_have_text([m["title"] for m in yuri["mangas"]])
    expect(first.locator(".org-item.in")).to_have_count(0)

    adds = [m["title"] for m in school["mangas"] if not m["assigned"]]
    [kept] = [m["title"] for m in school["mangas"] if m["assigned"]]
    expect(second.locator("h3")).to_have_text("校园")
    expect(second.locator(".org-state")).to_have_text("已有分类")
    expect(second.locator(".org-count")).to_have_text(f"加入 {len(adds)} 部 · 1 部已在")
    expect(second.locator(".org-item:not(.in)")).to_have_text(adds)  # what it gains comes first
    expect(second.locator(".org-item.in")).to_contain_text(kept)
    expect(second.locator(".org-item.in")).to_have_attribute("title", f"{kept} · 已在分类里")

    expect(dialog.locator(".org-unmapped")).to_have_text("书架里 1 部没对上 Bangumi 作品")
    expect(dialog.get_by_role("button", name="应用")).to_be_visible()
    expect(dialog.get_by_role("button", name="取消")).to_be_focused()  # not 应用
    page.wait_for_timeout(WAIT_MS)
    assert applies(fake) == [False]


def test_apply_goes_out_once_on_the_click_and_links_each_category(
    page: Page, fake: Fake, base_url: str
) -> None:
    fake.organize_delay_s = 0.3
    page.goto("/?t=百合,校园")
    page.locator(BUTTON).click()
    dialog = page.locator(DIALOG)
    apply = dialog.get_by_role("button", name="应用")
    expect(apply).to_be_visible()
    assert applies(fake) == [False]

    apply.dblclick()
    expect(dialog.get_by_role("button", name="应用中…")).to_be_disabled()
    expect(dialog.get_by_role("button", name="取消")).to_be_disabled()
    page.keyboard.press("Escape")  # the dialog waits for the write's answer
    msg = dialog.locator(".org-msg")
    expect(msg).to_have_text("新建 1 个分类 · 加入 5 部")
    expect(apply).to_be_hidden()
    page.wait_for_timeout(WAIT_MS)
    assert applies(fake) == [False, True]

    ids = {name: cid for cid, name in fake.shelf.categories.items()}
    yuri, school = ids["百合"], ids["校园"]
    rows = dialog.locator(".org-body .org-cat")
    expect(rows).to_have_count(2)
    expect(rows.nth(0).locator(".org-state")).to_have_text("新建分类")
    expect(rows.nth(1).locator(".org-state")).to_have_text("已有分类")
    suwayomi = base_url + SUWAYOMI_PATH
    links = dialog.locator(".org-body a.ext-link")
    expect(links).to_have_text(["在 Suwayomi 打开「百合」 ↗", "在 Suwayomi 打开「校园」 ↗"])
    expect(links.nth(0)).to_have_attribute("href", f"{suwayomi}/library?tab={yuri}")
    expect(links.nth(1)).to_have_attribute("href", f"{suwayomi}/library?tab={school}")
    expect(links.nth(0)).to_be_focused()
    with page.context.expect_page() as tab:
        links.nth(0).click()
    expect(tab.value).to_have_url(f"{suwayomi}/library?tab={yuri}")

    dialog.get_by_role("button", name="关闭").click()
    expect(dialog).to_have_count(0)
    page.locator(BUTTON).click()  # everything is filed now
    expect(dialog.locator(".org-msg")).to_have_text("没有要改动的")
    expect(dialog.locator(".org-group .org-state")).to_have_text(["已有分类", "已有分类"])
    expect(apply).to_have_count(0)
    page.wait_for_timeout(WAIT_MS)
    assert applies(fake) == [False, True, False]


def test_the_keyboard_opens_applies_and_closes(page: Page, fake: Fake) -> None:
    page.goto("/?t=百合")
    button = page.locator(BUTTON)
    button.focus()
    page.keyboard.press("Enter")
    dialog = page.locator(DIALOG)
    expect(dialog.get_by_role("button", name="应用")).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(button).to_be_focused()

    page.keyboard.press("Enter")
    apply = dialog.get_by_role("button", name="应用")
    expect(apply).to_be_visible()
    expect(dialog.get_by_role("button", name="取消")).to_be_focused()
    page.keyboard.press("Tab")
    expect(apply).to_be_focused()
    page.keyboard.press("Enter")
    expect(dialog.locator(".org-body a.ext-link")).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(button).to_be_focused()
    assert applies(fake) == [False, False, True]


def test_errors_show_and_an_apply_is_never_retried(page: Page, fake: Fake) -> None:
    page.goto("/?t=百合")
    button = page.locator(BUTTON)
    expect(button).to_be_enabled()
    fake.suwayomi_up = False  # after the page saw it up
    button.click()
    dialog = page.locator(DIALOG)
    msg = dialog.locator(".org-msg.error")
    expect(msg).to_have_text("整理失败 · Suwayomi unreachable")
    expect(dialog.get_by_role("button", name="应用")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(button).to_be_focused()

    fake.suwayomi_up = True
    button.click()
    apply = dialog.get_by_role("button", name="应用")
    expect(apply).to_be_visible()
    fake.suwayomi_up = False
    apply.click()
    expect(msg).to_have_text("整理失败 · Suwayomi unreachable")
    expect(apply).to_have_count(0)
    expect(dialog.get_by_role("button", name="关闭")).to_be_focused()
    page.wait_for_timeout(WAIT_MS)
    assert applies(fake) == [False, False, True]
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(button).to_be_focused()


def test_nothing_to_file_offers_no_apply(page: Page, fake: Fake) -> None:
    assert not fake.organize(["恐怖"], apply=False)["categories"][0]["mangas"]
    page.goto("/?t=恐怖")
    page.locator(BUTTON).click()
    dialog = page.locator(DIALOG)
    group = dialog.locator(".org-group")
    expect(group.locator(".org-count")).to_have_text("书架里没有")
    expect(group.locator(".org-state")).to_have_count(0)  # nothing would be made
    expect(dialog.locator(".org-msg")).to_have_text("没有要改动的")
    expect(dialog.get_by_role("button", name="应用")).to_have_count(0)
    dialog.get_by_role("button", name="关闭").click()
    expect(dialog).to_have_count(0)
    assert applies(fake) == [False]
