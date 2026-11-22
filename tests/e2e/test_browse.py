"""Browse page: search syntax, chips, URL state, facets, single-tag header, results."""

from __future__ import annotations

import re
import time

import pytest
from playwright.sync_api import Page, Route, expect

from .fake_backend import ROW_TAGS, Fake, Params
from .helpers import has_query, query, total_text

pytestmark = pytest.mark.e2e

INC = re.compile(r"\binc\b")
EXC = re.compile(r"\bexc\b")
NAMES = "els => els.map(e => e.dataset.name)"  # the chips' names, for evaluate_all
# Work 101's tags as a row ranks them: official by tag id, then user tags by votes.
TAGS_OF_101 = ["百合", "校园", "恋爱", "神作", "治愈", "少女", "日常", "青春", "GL"]


def chip_names(page: Page) -> list[str]:
    names: list[str] = page.locator(".conds .cond").evaluate_all(NAMES)
    return names


def test_syntax_makes_chips_and_url_round_trips(page: Page, fake: Fake) -> None:
    page.goto("/")
    summary = page.locator(".summary b")
    expect(summary).to_have_text(total_text(fake))
    expect(page.locator(".results .summary")).to_have_text(total_text(fake))  # nothing else
    expect(page.locator("#banner")).to_be_hidden()

    box = page.locator("#q")
    box.fill("#百合 #校园 -#恐怖")
    box.press("Enter")
    page.wait_for_url(has_query(t="百合,校园", x="恐怖"))
    expect(page.locator(".conds .cond.inc")).to_have_count(2)
    expect(page.locator('.conds .cond.exc[data-name="恐怖"]')).to_be_visible()
    assert chip_names(page) == ["百合", "校园", "恐怖"]
    expect(summary).to_have_text(total_text(fake, t="百合,校园", x="恐怖"))
    cards = page.locator(".grid a.card")
    expect(cards.filter(has_text="终将成为你")).to_have_count(1)
    expect(cards.filter(has_text="学园孤岛")).to_have_count(0)  # tagged 恐怖
    expect(box).to_have_value("")

    page.reload()
    expect(page.locator(".conds .cond")).to_have_count(3)
    expect(page.locator('.facets .chip.exc[data-name="恐怖"]')).to_be_visible()
    expect(summary).to_have_text(total_text(fake, t="百合,校园", x="恐怖"))

    page.go_back()
    page.wait_for_url(has_query(t=None, x=None))
    expect(page.locator(".conds")).to_have_count(0)
    expect(summary).to_have_text(total_text(fake))
    page.go_forward()
    page.wait_for_url(has_query(t="百合,校园", x="恐怖"))
    expect(page.locator(".conds .cond")).to_have_count(3)


def test_text_search_keeps_text_and_sorts_by_relevance(page: Page, fake: Fake) -> None:
    page.goto("/")
    box = page.locator("#q")
    box.fill("终将")
    box.press("Enter")
    page.wait_for_url(has_query(q="终将"))
    expect(box).to_have_value("终将")
    expect(page.locator('.seg [data-sort="rel"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".summary b")).to_have_text(total_text(fake, q="终将", sort="rel"))
    expect(page.locator(".grid a.card").first).to_contain_text("终将成为你")


def test_facet_chip_cycles_include_exclude_off(page: Page, fake: Fake) -> None:
    page.goto("/")
    page.locator('.facet[data-group="tag:genre"] .more').click()
    chip = page.locator('.facets .chip[data-name="校园"]')
    expect(chip).to_have_attribute("aria-label", re.compile("未选"))

    chip.click()
    page.wait_for_url(has_query(t="校园", x=None))
    expect(chip).to_have_class(INC)
    expect(page.locator(".summary b")).to_have_text(total_text(fake, t="校园"))

    chip.click()
    page.wait_for_url(has_query(t=None, x="校园"))
    expect(chip).to_have_class(EXC)
    expect(chip).to_have_attribute("aria-label", re.compile("已排除"))
    expect(page.locator(".summary b")).to_have_text(total_text(fake, x="校园"))

    chip.click()
    page.wait_for_url(has_query(t=None, x=None))
    expect(chip).not_to_have_class(re.compile(r"\b(inc|exc)\b"))

    # A condition chip flips include <-> exclude; its x removes it.
    page.goto("/?t=百合")
    page.locator('.conds .cond[data-name="百合"] .cond-label').click()
    page.wait_for_url(has_query(t=None, x="百合"))
    page.locator('.conds .cond[data-name="百合"] .cond-x').click()
    page.wait_for_url(has_query(t=None, x=None))


def tag_groups(page: Page) -> list[str]:
    groups: list[str] = page.locator(".facets .facet").evaluate_all(
        "els => els.map(e => e.dataset.group).filter(g => g.startsWith('tag:'))"
    )
    return groups


def test_selected_tag_chips_stay_in_their_own_group(page: Page, fake: Fake) -> None:
    page.goto("/")
    page.locator('.facet[data-group="tag:genre"] .more').click()
    page.locator('.facet[data-group="tag:genre"] .chip[data-name="校园"]').click()
    page.wait_for_url(has_query(t="校园"))
    page.locator('.facet[data-group="tag:audience"] .chip[data-name="GL"]').click()
    page.wait_for_url(has_query(t="校园,GL"))

    audience = page.locator('.facet[data-group="tag:audience"]')
    genre = page.locator('.facet[data-group="tag:genre"]')
    expect(audience.locator('.chip.inc[data-name="GL"]')).to_be_visible()  # 受众, not 题材
    expect(genre.locator('.chip.inc[data-name="校园"]')).to_be_visible()
    expect(genre.locator('.chip[data-name="GL"]')).to_have_count(0)
    expect(audience.locator('.chip[data-name="校园"]')).to_have_count(0)


def test_a_group_only_the_selection_fills_is_made_in_panel_order(page: Page, fake: Fake) -> None:
    params = {"t": "GL,百合", "x": "少女"}  # no other 受众 tag is left among the results
    removed = fake.search(Params.parse(params))["facets"]["excluded"][0]["n"]
    page.goto("/?t=GL,百合&x=少女")
    expect(page.locator(".conds .cond")).to_have_count(3)
    assert tag_groups(page) == ["tag:genre", "tag:audience"]

    audience = page.locator('.facet[data-group="tag:audience"] .chip')
    expect(audience).to_have_count(2)
    assert audience.evaluate_all(NAMES) == ["GL", "少女"]
    expect(audience.nth(0)).to_have_class(INC)
    expect(audience.nth(1)).to_have_class(EXC)
    expect(audience.nth(1).locator(".n")).to_have_text(f"−{removed}")  # what the exclusion removes
    genre = page.locator('.facet[data-group="tag:genre"] .chip')
    expect(genre.first).to_have_attribute("data-name", "百合")  # selected first
    expect(genre.first).to_have_class(INC)


def test_a_spelling_the_server_folds_keeps_its_group_and_count(page: Page, fake: Fake) -> None:
    # 校園 is 校园 in Traditional script, 爱情 a synonym of 恋爱, gl is GL in lower case.
    page.goto("/?t=校園,爱情&x=gl")
    expect(page.locator(".conds .cond")).to_have_count(3)
    expect(page.locator(".summary b")).to_have_text(total_text(fake, t="校園,爱情", x="gl"))
    # 受众 exists only through gl; 维基 comes last
    assert tag_groups(page) == ["tag:genre", "tag:audience", "tag:jawiki"]

    genre = page.locator('.facet[data-group="tag:genre"] .chip.inc')
    assert genre.evaluate_all(NAMES) == ["校園", "爱情"]  # the labels stay as the URL spelled them
    gl = page.locator('.facet[data-group="tag:audience"] .chip.exc[data-name="gl"]')
    expect(gl.locator(".n")).to_have_text("−2")  # what it removes: 101 and 108 carry GL


def test_a_search_that_cannot_match_keeps_the_known_tags_in_their_group(
    page: Page, fake: Fake
) -> None:
    page.goto("/?t=百合,没有这个标签")
    expect(page.locator(".empty-title")).to_have_text("无结果")
    expect(page.locator(".conds .cond")).to_have_count(2)
    assert tag_groups(page) == ["tag:genre", "tag:tag"]  # nothing else is left to fill the panel
    genre = page.locator('.facet[data-group="tag:genre"] .chip')
    assert genre.evaluate_all(NAMES) == ["百合"]
    other = page.locator('.facet[data-group="tag:tag"] .chip')
    assert other.evaluate_all(NAMES) == ["没有这个标签"]  # unknown to the catalogue: the default


PERSON_CHIP = ".conds .cond.author .cond-label"


def test_a_publisher_or_magazine_chip_says_what_it_is(page: Page, fake: Fake) -> None:
    chip = page.locator(PERSON_CHIP)
    page.goto("/work/101")
    page.locator(".work-pub a.plink", has_text="连载杂志").click()
    page.wait_for_url(has_query(a="91"))
    expect(chip).to_have_text("连载杂志 月刊コミック電撃大王")
    expect(page).to_have_title("连载杂志 月刊コミック電撃大王 · MangaAtlas")

    page.go_back()
    page.locator(".work-pub a.plink", has_text="出版社").click()
    page.wait_for_url(has_query(a="90"))
    expect(chip).to_have_text("出版社 KADOKAWA")

    page.go_back()  # a creator keeps 作者; 仲谷鳰 is 作者 once and 原作 once: the lower code
    page.locator(".work-meta a.plink", has_text="仲谷鳰").click()
    page.wait_for_url(has_query(a="1"))
    expect(chip).to_have_text("作者 仲谷鳰")


def test_a_bookmarked_person_filter_gets_its_name_and_role(page: Page, fake: Fake) -> None:
    page.goto("/?a=91")  # nothing remembered about 91 yet
    expect(page.locator(PERSON_CHIP)).to_have_text("连载杂志 月刊コミック電撃大王")
    expect(page).to_have_title("连载杂志 月刊コミック電撃大王 · MangaAtlas")
    page.goto("/?a=1")
    expect(page.locator(PERSON_CHIP)).to_have_text("作者 仲谷鳰")


def test_names_saved_by_earlier_visits_still_label_the_chip(page: Page, fake: Fake) -> None:
    def names_no_one(route: Route) -> None:
        response = route.fetch()
        body = response.json()
        del body["facets"]["people"]
        route.fulfill(response=response, json=body)

    page.route("**/api/search*", names_no_one)  # so the labels can only come from what is saved
    page.goto("/")
    page.evaluate(
        "localStorage.setItem('manga-atlas.people', JSON.stringify("
        "[['1', {name: '仲谷鳰'}], ['91', {name: '旧名', role: '连载杂志', creator: false}]]))"
    )
    page.goto("/?a=1")
    expect(page.locator(PERSON_CHIP)).to_have_text("作者 仲谷鳰")  # a name saved without a role

    page.goto("/work/101")  # a work page knows names only: it must keep the role it was told
    expect(page.locator("h1.work-title")).to_have_text("终将成为你")
    page.goto("/?a=91")
    expect(page.locator(PERSON_CHIP)).to_have_text("连载杂志 月刊コミック電撃大王")


def test_a_chip_of_an_unknown_group_lands_in_the_default_one(page: Page, fake: Fake) -> None:
    def regroup(route: Route) -> None:
        response = route.fetch()
        body = response.json()
        body["facets"]["included"] = [
            {"query": "校园", "name": "校园", "grp": "status"},  # one the panel shows elsewhere
            {"query": "百合", "name": "百合", "grp": "no-such-group"},
        ]
        route.fulfill(response=response, json=body)

    page.route("**/api/search*", regroup)
    page.goto("/?t=校园,百合")
    expect(page.locator(".conds .cond")).to_have_count(2)
    chips = page.locator('.facet[data-group="tag:tag"] .chip.inc')
    expect(chips).to_have_count(2)
    assert chips.evaluate_all(NAMES) == ["校园", "百合"]
    expect(page.locator('.facet[data-group="tag:genre"] .chip.inc')).to_have_count(0)


def test_tags_only_wikipedia_gives_have_their_own_group(page: Page, fake: Fake) -> None:
    page.goto("/")
    groups = tag_groups(page)
    assert groups[-2:] == ["tag:jawiki", "tag:yurimiru"]  # after the vocabulary's groups
    wiki = page.locator('.facet[data-group="tag:jawiki"]')
    expect(wiki.locator("h3")).to_have_text("维基")
    assert wiki.locator(".chip").evaluate_all(NAMES) == ["高等学校（舞台）", "ディストピア", "悪魔"]
    expect(wiki.locator('.chip[data-name="高等学校（舞台）"] .n')).to_have_text("3")

    wiki.locator('.chip[data-name="悪魔"]').click()
    page.wait_for_url(has_query(t="悪魔"))
    expect(page.locator(".summary b")).to_have_text(total_text(fake, t="悪魔"))
    expect(page.locator(".grid a.card")).to_have_count(1)
    expect(page.locator('.grid a.card[data-id="205"]')).to_be_visible()
    # chosen, it stays in its own group rather than the default one
    expect(wiki.locator('.chip.inc[data-name="悪魔"]')).to_be_visible()
    expect(page.locator('.facet[data-group="tag:tag"] .chip[data-name="悪魔"]')).to_have_count(0)


def test_tags_only_yurimiru_gives_have_their_own_group(page: Page, fake: Fake) -> None:
    page.goto("/")
    yuri = page.locator('.facet[data-group="tag:yurimiru"]')
    expect(yuri.locator("h3")).to_have_text("百合细分")
    assert sorted(yuri.locator(".chip").evaluate_all(NAMES)) == ["前后辈", "社会人"]

    yuri.locator('.chip[data-name="社会人"]').click()
    page.wait_for_url(has_query(t="社会人"))
    expect(page.locator(".summary b")).to_have_text(total_text(fake, t="社会人"))
    expect(page.locator('.grid a.card[data-id="106"]')).to_be_visible()
    expect(yuri.locator('.chip.inc[data-name="社会人"]')).to_be_visible()


def test_panel_controls_write_the_url(page: Page, fake: Fake) -> None:
    page.goto("/")
    page.locator('.facet[data-group="region"] .chip[data-name="中国"]').click()
    page.wait_for_url(has_query(region="中国"))
    year_from = page.locator(".year-in").first
    year_from.fill("2015")
    year_from.press("Enter")
    page.wait_for_url(has_query(region="中国", y="2015-"))
    page.locator('[data-min="7"]').click()
    page.wait_for_url(has_query(region="中国", y="2015-", min="7"))
    expect(page.locator(".summary b")).to_have_text(
        total_text(fake, region="中国", y="2015-", min="7")
    )
    expect(page.locator(".conds .cond")).to_have_count(3)

    page.locator("#read-wrap").click()
    page.wait_for_url(has_query(region="中国", read="1"))
    page.locator(".conds .clear").click()
    page.wait_for_url(has_query(region=None, y=None, min=None, read="1"))  # 清除 keeps 只看能读
    expect(page.locator(".summary b")).to_have_text(total_text(fake, read="1"))


def test_single_tag_header(page: Page, fake: Fake) -> None:
    header = fake.tag_header("百合")
    page.goto("/?t=百合")
    head = page.locator(".tag-head")
    expect(head.locator("h1")).to_have_text("百合")
    expect(head.locator(".th-counts")).to_contain_text(f"{header['tag']['n']} 部")
    expect(head.locator(".rchip")).to_have_count(len(header["related"]))

    head.locator('.rchip[data-name="校园"] button.plus').click()
    page.wait_for_url(has_query(t="百合,校园"))
    expect(page.locator(".tag-head")).to_have_count(0)

    page.go_back()
    head.locator(".th-authors button", has_text="仲谷鳰").click()
    page.wait_for_url(has_query(t="百合", a="1"))
    expect(page.locator(".conds .cond.author")).to_contain_text("仲谷鳰")
    expect(page.locator(".summary b")).to_have_text(total_text(fake, t="百合", a="1"))

    page.go_back()
    head.locator('.rchip[data-name="恋爱"] a').click()
    page.wait_for_url(has_query(t="恋爱"))
    expect(head.locator("h1")).to_have_text("恋爱")


def test_zero_results_offer_relaxing(page: Page, fake: Fake) -> None:
    page.goto("/?t=百合,恐怖,科幻")
    expect(page.locator(".empty-title")).to_have_text("无结果")
    relax = page.locator('.relax-chip[data-name="科幻"]')
    expect(relax).to_contain_text(f"{fake.search(Params.parse({'t': '百合,恐怖'}))['total']} 部")
    relax.click()
    page.wait_for_url(has_query(t="百合,恐怖"))
    expect(page.locator(".grid a.card").filter(has_text="学园孤岛")).to_have_count(1)


def test_infinite_scroll_list_view_and_cover_placeholder(page: Page, fake: Fake) -> None:
    total = fake.search(Params())["total"]
    page.goto("/")
    cards = page.locator(".grid a.card")
    expect(cards.first).to_be_visible()
    assert cards.count() < total

    page.locator(".sentinel").scroll_into_view_if_needed()
    expect(cards).to_have_count(total)
    expect(page.locator(".sentinel")).to_have_text("已到底")

    no_cover = cards.filter(has_text="樱Trick")  # its cover is a 404
    no_cover.scroll_into_view_if_needed()
    expect(no_cover.locator("img")).to_have_count(0)
    expect(no_cover.locator(".ph")).to_have_text("樱")

    page.locator('[data-view="list"]').click()
    page.wait_for_url(has_query(view="list"))
    first = page.locator(".list a.card.row").first
    expect(first).to_contain_text("钢之炼金术师")
    expect(first).to_contain_text("鋼の錬金術師")


CAP = 30  # longest page the fake server returns in the deep-scroll tests, whatever is asked
FAR = 80  # a result well past two pages


def open_work_far_down(page: Page, total: int) -> int:
    """Scrolls every result in, opens the work at FAR and returns its id."""
    cards = page.locator(".grid a.card")
    while cards.count() < total:
        seen = cards.count()
        page.locator(".sentinel").scroll_into_view_if_needed()
        page.wait_for_function(
            "n => document.querySelectorAll('.grid a.card').length > n", arg=seen
        )
    card = cards.nth(FAR)
    wid = int(card.get_attribute("data-id") or 0)
    card.click()
    page.wait_for_url(f"**/work/{wid}")
    expect(page.locator(".work-nav .pos")).to_have_text(f"第 {FAR + 1} / {total} 部")
    return wid


def test_back_restores_scroll_and_selection_past_the_servers_page_cap(
    page: Page, fake: Fake
) -> None:
    fake.search_max = CAP
    total = fake.search(Params())["total"]
    assert FAR > 2 * CAP and total > FAR
    page.goto("/")
    wid = open_work_far_down(page, total)
    asked = len(fake.searches)

    page.go_back()
    card = page.locator(f'.grid a.card[data-id="{wid}"]')
    expect(card).to_be_focused()  # the work that was opened is selected again
    expect(card).to_be_in_viewport()
    expect(page.locator(".grid a.card")).to_have_count(total)
    assert len(fake.searches) == asked  # from the results kept in memory
    assert abs(page.evaluate("window.scrollY - history.state.scrollY")) < 2


def test_a_deep_scroll_is_refetched_in_as_many_requests_as_the_cap_needs(
    page: Page, fake: Fake
) -> None:
    fake.search_max = CAP
    everything = [it["id"] for it in fake.search(Params(limit=1000))["items"]]
    total = len(everything)
    page.goto("/")
    wid = open_work_far_down(page, total)
    page.reload()  # the results kept in memory are gone, as they are once they expire
    expect(page.locator(".work-nav .pos")).to_have_text(f"第 {FAR + 1} / {total} 部")
    asked = len(fake.searches)

    page.go_back()
    cards = page.locator(".grid a.card")
    expect(cards).to_have_count(total)
    # Everything saved is asked for, then the rest from wherever the server stopped.
    assert [(p.offset, p.limit) for p in fake.searches[asked:]] == [
        (offset, total - offset) for offset in range(0, total, CAP)
    ]
    assert cards.evaluate_all("els => els.map(e => Number(e.dataset.id))") == everything
    expect(page.locator(f'.grid a.card[data-id="{wid}"]')).to_be_in_viewport()
    assert abs(page.evaluate("window.scrollY - history.state.scrollY")) < 2


def test_a_failed_follow_up_keeps_what_was_fetched(page: Page, fake: Fake) -> None:
    fake.search_max = CAP
    total = fake.search(Params())["total"]
    page.goto("/")
    open_work_far_down(page, total)
    page.reload()
    expect(page.locator(".work-nav .pos")).to_have_text(f"第 {FAR + 1} / {total} 部")

    def fail_follow_ups(route: Route) -> None:
        if query(route.request.url).get("offset") == ["0"]:
            route.continue_()
        else:
            route.abort()

    page.route("**/api/search*", fail_follow_ups)
    page.go_back()
    expect(page.locator(".grid a.card")).to_have_count(CAP)  # the first page, not an error screen
    expect(page.locator(".empty-title")).to_have_count(0)
    expect(page.locator(".sentinel")).to_contain_text("加载失败")  # scrolling can retry the rest


def test_leaving_during_a_deep_restore_stops_asking(page: Page, fake: Fake) -> None:
    fake.search_max = CAP
    total = fake.search(Params(sort="heat"))["total"]
    page.goto("/?sort=heat")
    open_work_far_down(page, total)
    page.reload()
    expect(page.locator(".work-nav .pos")).to_have_text(f"第 {FAR + 1} / {total} 部")
    fake.search_delay_s = 0.5
    asked = len(fake.searches)

    page.go_back()
    deadline = time.monotonic() + 10
    while len(fake.searches) - asked < 2:  # the first answer is in, the second request is out
        assert time.monotonic() < deadline
        page.wait_for_timeout(20)
    page.locator("#brand").click()  # leave for the home page
    expect(page.locator('.seg [data-sort="rank"]')).to_have_attribute("aria-pressed", "true")
    page.wait_for_timeout(1500)  # long enough for the remaining requests, were they still sent
    restore = [p for p in fake.searches[asked:] if p.sort == "heat"]
    assert [p.offset for p in restore] == [0, CAP]
    expect(page.locator('.seg [data-sort="rank"]')).to_have_attribute("aria-pressed", "true")


def test_list_rows_show_authors_tags_and_summary(page: Page, fake: Fake) -> None:
    page.goto("/?t=百合&view=list")
    row = page.locator('.list a.card.row[data-id="101"]')
    expect(row.locator(".meta")).to_contain_text("仲谷鳰")
    expect(row.locator(".meta")).not_to_contain_text("KADOKAWA")  # a publisher is no author
    expect(row.locator(".tags .tag")).to_have_text(TAGS_OF_101[:ROW_TAGS])
    expect(row.locator(".summary")).to_contain_text("不懂恋爱心动的高一新生")

    page.locator('[data-view="grid"]').click()
    page.wait_for_url(has_query(view=None))
    card = page.locator('.grid a.card[data-id="101"]')
    expect(card).to_be_visible()
    expect(card).not_to_contain_text("仲谷鳰")  # the grid stays as it was
    expect(card.locator(".tags, .summary")).to_have_count(0)


def test_list_rows_leave_out_region_and_status_tags(page: Page, fake: Fake) -> None:
    sent: list[int] = []

    def add_region_tag(route: Route) -> None:
        response = route.fetch()
        body = response.json()
        for item in body["items"]:
            item["tags"].append({"name": "日本", "grp": "region"})
        sent.append(len(body["items"]))
        route.fulfill(response=response, json=body)

    page.route("**/api/search*", add_region_tag)
    page.goto("/?t=百合&view=list")
    tags = page.locator('.list a.card.row[data-id="101"] .tags .tag')
    expect(tags).to_have_text(TAGS_OF_101[:ROW_TAGS])
    assert sent and sent[0] > 0  # the extra tag did reach the page


def test_readable_only_results_follow_new_checks(page: Page, fake: Fake) -> None:
    fake.step_s = 0.01
    page.goto("/?read=1")
    expect(page.locator(".grid a.card").first).to_be_visible()
    expect(page.locator('a.card[data-id="101"]')).to_have_count(0)  # never checked
    page.locator("#read-wrap").click()
    page.wait_for_url(has_query(read=None))
    page.locator('a.card[data-id="101"]').click()
    expect(page.locator(".where li.src.yes")).to_have_count(1)
    expect(page.locator(".where li.src.checking")).to_have_count(0)
    page.go_back()
    page.locator("#read-wrap").click()
    page.wait_for_url(has_query(read="1"))
    expect(page.locator('a.card[data-id="101"]')).to_have_count(1)


def test_favorites_only_and_by_when_added(page: Page, fake: Fake) -> None:
    fake.favorites.update({104: 1, 209: 2, 101: 3})  # 101 added last
    page.goto("/")
    expect(page.locator('a.card[data-id="101"] .fav-mark')).to_have_text("★")
    expect(page.locator('a.card[data-id="201"] .fav-mark')).to_have_count(0)

    page.locator("#fav-wrap").click()
    page.wait_for_url(has_query(fav="1"))
    expect(page.locator(".summary b")).to_have_text(total_text(fake, fav="1"))
    page.locator('[data-sort="fav"]').click()
    page.wait_for_url(has_query(fav="1", sort="fav"))
    expect(page.locator('[data-sort="fav"]')).to_have_class(re.compile(r"\bon\b"))
    ids = page.locator(".grid a.card").evaluate_all("els => els.map(e => Number(e.dataset.id))")
    assert ids == [101, 209, 104]

    page.locator('[data-min="7"]').click()
    page.wait_for_url(has_query(fav="1", min="7"))
    page.locator(".conds .clear").click()
    page.wait_for_url(has_query(fav="1", sort="fav", min=None))  # 清除 keeps 只看收藏


def test_a_favorite_set_on_a_work_page_shows_on_returning(page: Page, fake: Fake) -> None:
    card = page.locator('a.card[data-id="201"]')
    page.goto("/")
    card.click()
    page.wait_for_url("**/work/201")
    with page.expect_response("**/api/favorites/201"):
        page.locator(".fav-toggle").click()
    searches = len(fake.searches)
    page.go_back()
    expect(card.locator(".fav-mark")).to_be_visible()
    assert len(fake.searches) == searches  # the kept results, brought up to date

    page.locator("#fav-wrap").click()
    page.wait_for_url(has_query(fav="1"))
    expect(page.locator(".grid a.card")).to_have_count(1)
    card.click()
    page.wait_for_url("**/work/201")
    with page.expect_response("**/api/favorites/201"):
        page.locator(".fav-toggle").click()
    page.go_back()
    expect(page.locator(".empty-title")).to_have_text("无结果")  # asked again, now without it


def test_check_page_splits_by_the_servers_limit(page: Page, fake: Fake) -> None:
    fake.step_s = 0.01
    fake.check_batch_max = 4
    items = fake.search(Params.parse({"t": "百合"}))["items"]
    unknown = [it["id"] for it in items if it["readable"] == "unknown"]
    assert len(unknown) > fake.check_batch_max
    page.goto("/?t=百合")
    page.locator("button.check").click()
    expect(page.locator('a.card[data-id="107"] .badge.yes')).to_be_visible()
    expect(page.locator("button.check")).to_have_text("检查本页")
    assert all(len(b) <= fake.check_batch_max for b in fake.batches)
    assert [i for b in fake.batches for i in b] == unknown


def test_check_page_never_goes_out_without_a_limit(page: Page, fake: Fake) -> None:
    fake.step_s = 0.01
    fake.advertise_limit = False
    page.goto("/?t=百合")
    button = page.locator("button.check")
    button.click()
    page.wait_for_load_state("networkidle")
    expect(button).to_have_text("检查本页")
    assert fake.batches == []  # no limit known: nothing sent

    fake.advertise_limit = True
    fake.check_batch_max = 4
    button.click()  # the limit is fetched before sending
    expect(page.locator('a.card[data-id="107"] .badge.yes')).to_be_visible()
    assert fake.batches and all(len(b) <= 4 for b in fake.batches)


def test_check_page_updates_badges(page: Page, fake: Fake) -> None:
    fake.step_s = 0.01
    params = {"t": "百合"}
    unknown = [
        it["id"] for it in fake.search(Params.parse(params))["items"] if it["readable"] == "unknown"
    ]
    page.goto("/?t=百合")
    button = page.locator("button.check")
    expect(page.locator('a.card[data-id="107"] .badge')).to_have_count(0)

    button.click()
    expect(page.locator('a.card[data-id="107"] .badge.yes')).to_be_visible()
    expect(button).to_have_text("检查本页")
    assert fake.batches == [unknown]
