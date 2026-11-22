"""Work page: details, 在哪读 streaming, read links, Suwayomi-down banner."""

from __future__ import annotations

import re
from urllib.parse import quote

import pytest
from playwright.sync_api import Locator, Page, expect

from manga_atlas.build.tagsources.jawiki import article_url
from manga_atlas.config import Settings

from .fake_backend import SUWAYOMI_PATH, Fake
from .helpers import has_query, total_text

pytestmark = pytest.mark.e2e


def state(name: str) -> re.Pattern[str]:
    return re.compile(rf"\b{name}\b")


def test_where_to_read_streams_rows(page: Page, fake: Fake) -> None:
    fake.step_s = 0.5
    page.goto("/work/101")
    rows = page.locator(".where li.src")
    ids = {s["name"]: s["id"] for s in fake.catalog.sources}

    def row(name: str) -> Locator:
        return page.locator(f'.where li.src[data-source="{ids[name]}"]')

    expect(rows).to_have_count(len(fake.catalog.sources))
    expect(rows.nth(0)).to_have_class(state("checking"))
    expect(rows.nth(0).locator(".src-name")).to_have_text("中文源甲")

    expect(row("中文源甲")).to_have_class(state("no"))  # first line arrived...
    expect(row("Shonen Jump+")).to_have_class(state("checking"))  # ...later ones are pending

    expect(row("中文源乙")).to_have_class(state("yes"))
    expect(row("中文源乙").locator(".src-sub")).to_have_text("作者一致")
    expect(row("中文源丙")).to_have_class(state("maybe"))
    expect(row("中文源丙").locator(".mark")).to_have_text("≈")
    expect(row("中文源丙").locator(".src-sub")).to_have_text("仅书名一致「终将成为妳」")
    expect(row("Shonen Jump+")).to_have_class(state("down"))
    expect(row("Shonen Jump+").locator(".src-sub")).to_have_text("源不可达")
    expect(row("中文源甲").locator(".src-sub")).to_have_text("未找到")
    expect(page.locator(".where-sum")).to_have_text("2 个源可读")
    assert fake.checks == [(101, {})]
    # Where to read first, then what deserves a look, then the misses.
    expect(rows.locator(".src-name")).to_have_text(
        ["中文源乙", "中文源丙", "Shonen Jump+", "中文源甲"]
    )


def test_misses_fold_into_one_line(page: Page, fake: Fake) -> None:
    page.goto("/work/104")  # ≈ at 中文源甲; 乙 and 丙 miss; Shonen Jump+ unreachable
    rows = page.locator(".where li.src")
    expect(page.locator(".where li.src.checking")).to_have_count(0)
    expect(rows.locator(".src-name")).to_have_text(
        ["中文源甲", "Shonen Jump+", "其余 2 个源未找到"]
    )
    expect(rows.last.locator(".src-sub")).to_have_text("中文源乙、中文源丙")

    page.goto("/work/1001")  # nobody has it
    expect(page.locator(".where li.src.checking")).to_have_count(0)
    expect(rows.locator(".src-name")).to_have_text(["4 个源都未找到"])


def test_read_links_go_through_the_read_route(page: Page, fake: Fake, base_url: str) -> None:
    suwayomi = base_url + SUWAYOMI_PATH
    page.goto("/work/101")
    read = page.locator(".where li.src.yes a.read")
    expect(read).to_have_attribute("href", "/api/read/5101")
    expect(read).to_have_attribute("target", "_blank")
    expect(page.locator(".where li.src.maybe a.read")).to_have_attribute("href", "/api/read/5102")
    expect(page.locator(".where li.src.no a.read")).to_have_count(0)
    expect(page.locator(".where li.src.yes .r-hint")).to_be_visible()
    search = f"{suwayomi}/sources/all/search?query="
    expect(page.locator(".where a.global")).to_have_attribute(
        "href", search + quote("终将成为你", safe="")
    )
    expect(page.locator(".alias-search a").first).to_have_attribute(
        "href", search + quote("やがて君になる", safe="")
    )

    assert fake.reads == []  # nothing is asked until the link is followed
    with page.context.expect_page() as tab:
        read.click()
    expect(tab.value).to_have_url(f"{suwayomi}/manga/5101")  # the route sent the tab on
    assert fake.reads == [5101]


def test_title_matches_and_same_title_by_another_author(page: Page, fake: Fake) -> None:
    page.goto("/work/102")  # long title: a title match is ✓ even without an author
    rows = page.locator(".where li.src")
    expect(rows.nth(0).locator(".src-sub")).to_have_text("作者一致")
    expect(rows.nth(1)).to_have_class(state("yes"))
    expect(rows.nth(1).locator(".mark")).to_have_text("✓")
    expect(rows.nth(1).locator(".src-sub")).to_have_text("书名一致「安达与岛村」")

    page.goto("/work/103")  # short title, another author: listed, never read
    other = page.locator(".where li.src").nth(1)
    expect(other).to_have_class(state("no"))
    expect(other.locator(".src-sub")).to_have_text("同名不同作「青之花」")
    expect(other.locator("a")).to_have_count(0)
    expect(page.locator(".where-sum")).to_have_text("1 个源可读")


def test_expired_verdicts_are_not_offered(page: Page, fake: Fake) -> None:
    fake.step_s = 0.5
    page.goto("/work/203")  # its cached ✓ is past the TTL
    row = page.locator('.where li.src[data-source="zh-a"]')
    expect(row).to_have_class(state("stale"))
    expect(row.locator("a.read")).to_have_count(0)
    expect(page.locator(".where-sum")).to_have_text("")
    expect(row).to_have_class(state("yes"))  # the fresh line replaces it
    expect(row.locator("a.read")).to_be_visible()

    fake.reset()
    fake.fail_checks = True
    page.goto("/work/203")
    expect(page.locator(".where li.src.checking")).to_have_count(0)  # the check failed
    expect(row).to_have_class(state("stale"))
    expect(page.locator(".where a.read")).to_have_count(0)


def test_recheck_forces_a_new_check(page: Page, fake: Fake) -> None:
    page.goto("/work/101")
    expect(page.locator(".where li.src.yes")).to_have_count(1)
    expect(page.locator(".where .recheck")).to_be_enabled()
    fake.step_s = 0.3
    page.locator(".where .recheck").click()
    expect(page.locator(".where li.src.checking")).to_have_count(len(fake.catalog.sources))
    expect(page.locator(".where li.src.yes")).to_have_count(1)
    assert fake.checks == [(101, {}), (101, {"force": True})]


def test_details_tags_and_relations(page: Page, fake: Fake) -> None:
    page.goto("/work/101")
    expect(page.locator("h1.work-title")).to_have_text("终将成为你")
    expect(page.locator(".aliases")).to_contain_text("やがて君になる")
    expect(page.locator(".score-big")).to_have_text("8.9")
    expect(page.locator(".score-sub")).to_contain_text("排名 #12")
    official = page.locator(".tag-row").first.locator(".chip")
    expect(official).to_have_text(["百合", "校园", "恋爱"])
    expect(page.locator('.tag-row .chip[data-name="神作"] .n')).to_have_text("328")

    groups = page.locator(".rel-group h3")
    expect(groups).to_have_count(3)
    volumes = page.locator('.rel-group[aria-label="单行本"]')
    expect(volumes.locator("li:not(.more-wrap):visible")).to_have_count(6)
    volumes.locator(".more").click()
    expect(volumes.locator("li:not(.more-wrap):visible")).to_have_count(8)
    expect(page.locator('a.rel.ext[href="https://bgm.tv/subject/900101"]')).to_be_visible()

    page.locator('a.rel[href="/work/111"]').click()
    page.wait_for_url("**/work/111")
    expect(page.locator("h1.work-title")).to_have_text("终将成为你 官方漫画选集")

    page.locator(".work-meta a.plink", has_text="仲谷鳰").click()
    page.wait_for_url(has_query(a="1"))
    expect(page.locator(".conds .cond.author")).to_contain_text("仲谷鳰")


def test_wikipedia_tags_have_a_row_and_a_link_to_the_article(page: Page, fake: Fake) -> None:
    page.goto("/work/101")
    row = page.locator('.tag-row[data-source="jawiki"]')
    expect(row.locator(".kind")).to_have_text("维基")
    # merged tags by their catalogue name, the others in Japanese
    expect(row.locator(".chip")).to_have_text(["百合", "恋爱", "高等学校（舞台）"])
    expect(row.locator(".chip .n")).to_have_count(0)  # no votes behind these
    expect(row.locator(".chip.official")).to_have_count(0)  # official is Bangumi's word
    user = page.locator(".tag-row").nth(1)
    expect(user.locator(".kind")).to_have_text("用户")
    expect(user.locator('.chip[data-name="高等学校（舞台）"]')).to_have_count(0)

    article = row.locator("a.page-link")
    expect(article).to_have_text("条目 ↗")
    expect(article).to_have_attribute("href", article_url(Settings(), "やがて君になる"))
    expect(article).to_have_attribute("target", "_blank")
    expect(article).to_have_attribute("title", "日文维基「やがて君になる」")
    expect(page.locator(".work-foot")).to_contain_text("日文维基 CC BY-SA 4.0")

    row.locator('.chip[data-name="高等学校（舞台）"]').click()
    page.wait_for_url(has_query(t="高等学校（舞台）"))
    expect(page.locator(".summary b")).to_have_text(total_text(fake, t="高等学校（舞台）"))

    page.goto("/work/103")  # no article
    expect(page.locator("h1.work-title")).to_have_text("青之花")
    expect(page.locator('.tag-row[data-source="jawiki"]')).to_have_count(0)
    expect(page.locator(".work-foot")).not_to_contain_text("日文维基")


def test_yurimiru_tags_have_a_row_and_a_link_to_the_work_page(page: Page, fake: Fake) -> None:
    page.goto("/work/101")
    kinds = page.locator(".tag-row .kind")
    expect(kinds).to_have_text(["官方", "用户", "维基", "ゆりみる"])
    row = page.locator('.tag-row[data-source="yurimiru"]')
    expect(row.locator(".chip")).to_have_text(["百合", "校园", "前后辈"])
    expect(row.locator(".chip.official")).to_have_count(0)

    entry = fake.catalog.works[101]["pages"]["yurimiru"]
    link = row.locator("a.page-link")
    expect(link).to_have_text("作品页 ↗")
    expect(link).to_have_attribute("href", entry["url"])
    expect(link).to_have_attribute("title", "ゆりみる「やがて君になる」")
    expect(page.locator(".work-foot")).to_contain_text("日文维基 CC BY-SA 4.0 · ゆりみる")

    row.locator('.chip[data-name="前后辈"]').click()
    page.wait_for_url(has_query(t="前后辈"))
    expect(page.locator(".summary b")).to_have_text(total_text(fake, t="前后辈"))

    page.goto("/work/106")  # ゆりみる only
    expect(page.locator(".tag-row .kind")).to_have_text(["官方", "用户", "ゆりみる"])


def test_publisher_and_magazine_have_a_line_under_the_meta(page: Page, fake: Fake) -> None:
    page.goto("/work/101")  # 作者 + 出版社 + 连载杂志
    meta = page.locator(".work-meta")
    expect(meta).to_contain_text("仲谷鳰")
    expect(meta).not_to_contain_text("KADOKAWA")
    expect(meta).not_to_contain_text("月刊コミック電撃大王")
    expect(meta.locator(".role")).to_have_count(0)  # a lone author needs no label

    links = page.locator(".work-meta + .work-pub a.plink")  # directly under the meta line
    expect(links).to_have_count(2)
    expect(links.nth(0)).to_have_text("KADOKAWA出版社")
    expect(links.nth(0)).to_have_attribute("href", "/?a=90")
    expect(links.nth(1)).to_have_text("月刊コミック電撃大王连载杂志")
    expect(links.nth(1)).to_have_attribute("href", "/?a=91")

    links.nth(1).click()
    page.wait_for_url(has_query(a="91"))
    expect(page.locator(".summary b")).to_have_text(total_text(fake, a="91"))
    expect(page.locator('.grid a.card[data-id="101"]')).to_be_visible()


def test_roles_are_labelled_among_creators_only(page: Page, fake: Fake) -> None:
    page.goto("/work/202")  # 原作 and 作画, no publisher on record
    expect(page.locator(".work-meta .role")).to_have_text(["原作", "作画"])
    expect(page.locator(".work-pub")).to_have_count(0)

    page.goto("/work/102")
    expect(page.locator(".work-meta .plink").first).to_have_text("入间人间")
    expect(page.locator(".work-meta .role")).to_have_count(0)
    expect(page.locator(".work-pub")).to_have_count(0)


def test_slow_status_does_not_hold_up_the_work_page(page: Page, fake: Fake) -> None:
    fake.status_delay_s = 3
    page.goto("/work/101")
    expect(page.locator("h1.work-title")).to_have_text("终将成为你", timeout=2500)
    yes = page.locator(".where li.src.yes")
    expect(yes.locator(".src-name")).to_have_text("中文源乙")  # names fill in once status arrives
    expect(yes.locator("a.read")).to_have_attribute("href", "/api/read/5101")


def test_work_without_aliases_offers_only_the_global_search(page: Page, fake: Fake) -> None:
    page.goto("/work/1001")
    title = fake.catalog.works[1001]["title"]
    expect(page.locator(".where-fallback")).to_have_text(f"在 Suwayomi 全局搜索「{title}」 ↗")


def test_favorite_toggle_is_kept_and_taken_back_when_refused(page: Page, fake: Fake) -> None:
    saved = "**/api/favorites/101"
    page.goto("/work/101")
    toggle = page.locator(".work-head .fav-toggle")
    expect(toggle).to_have_text("☆收藏")
    with page.expect_response(saved) as sent:
        toggle.click()
    expect(toggle).to_have_class(state("on"))
    expect(toggle).to_have_text("★已收藏")
    assert sent.value.status == 204 and list(fake.favorites) == [101]

    page.reload()
    expect(toggle).to_have_text("★已收藏")  # as the server keeps it
    with page.expect_response(saved):
        page.keyboard.press("f")
    expect(toggle).to_have_text("☆收藏")
    assert fake.favorites == {}

    fake.fail_favorites = True
    with page.expect_response(saved) as sent:
        toggle.click()
    assert sent.value.status == 500
    expect(toggle).to_have_text("☆收藏")  # taken back
    expect(toggle).not_to_have_class(state("on"))


def test_unknown_work_says_so(page: Page, fake: Fake) -> None:
    page.goto("/work/999999")
    expect(page.locator(".empty-title")).to_have_text("无此作品")


def test_suwayomi_down_shows_banner_and_browsing_still_works(page: Page, fake: Fake) -> None:
    fake.suwayomi_up = False
    page.goto("/")
    banner = page.locator("#banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("Suwayomi")
    expect(page.locator("#suwa")).to_have_attribute("data-state", "down")
    expect(page.locator(".grid a.card").first).to_be_visible()
    expect(page.locator("button.check")).to_be_disabled()

    page.locator('.facets .chip[data-name="奇幻"]').click()
    page.wait_for_url(has_query(t="奇幻"))
    expect(page.locator(".grid a.card").first).to_be_visible()

    page.goto("/work/105")
    rows = page.locator(".where li.src")
    expect(rows).to_have_count(len(fake.catalog.sources))
    expect(page.locator(".where li.src.down")).to_have_count(len(fake.catalog.sources))
    expect(page.locator(".where a.read")).to_have_count(0)
    expect(banner).to_be_visible()

    page.goto("/work/102")  # verdicts cached before Suwayomi went away: listed, not offered
    expect(page.locator(".where li.src.yes")).to_have_count(2)
    expect(page.locator(".where li.src.checking")).to_have_count(0)
    expect(page.locator(".where a.read")).to_have_count(0)


def test_stepping_through_favorites_survives_taking_one_off(page: Page, fake: Fake) -> None:
    # The list ←/→ walk is the one the work was opened from, even after a favorite comes off it.
    fake.favorites.update({104: 1, 209: 2, 101: 3, 201: 4, 102: 5, 103: 6})
    page.goto("/?fav=1&sort=fav")
    cards = page.locator(".grid a.card")
    expect(cards).to_have_count(6)
    ids = cards.evaluate_all("els => els.map(e => Number(e.dataset.id))")
    page.locator(f'a.card[data-id="{ids[1]}"]').click()
    expect(page.locator(".work-nav .pos")).to_have_text("第 2 / 6 部")
    with page.expect_response(f"**/api/favorites/{ids[1]}"):
        page.locator(".fav-toggle").click()
    page.keyboard.press("ArrowRight")
    page.wait_for_url(f"**/work/{ids[2]}")
    expect(page.locator(".work-nav .pos")).to_have_text("第 3 / 6 部")
    page.keyboard.press("ArrowRight")
    page.wait_for_url(f"**/work/{ids[3]}")  # nothing skipped
    page.go_back()
    page.goto("/?fav=1&sort=fav")
    expect(cards).to_have_count(5)  # the list itself is fetched again
