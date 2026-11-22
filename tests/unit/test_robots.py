"""robots.txt read as RFC 9309 says, which Python's robotparser does not (it takes the first rule
that matches, so "Allow: /" above "Disallow: /api/" would let /api/ through)."""

import pytest

from manga_atlas.build.tagsources.common import Robots

SITE = """\
User-Agent: GPTBot
User-Agent: ClaudeBot  # a training crawler
Disallow: /

User-Agent: *
Allow: /
Disallow: /api/
Disallow: /mypage/
Disallow: /login

Sitemap: https://example.test/sitemap.xml
"""
AGENT = "manga-atlas/0.1 (+https://github.com/BANANASJIM/manga-atlas)"


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/sitemap.xml", True),
        ("/works/0067031b/vol/1", True),
        ("/works?tags=%E5%AD%A6%E5%9C%92", True),
        ("/", True),
        ("/api/works?offset=60", False),  # the longer rule wins over "Allow: /"
        ("/mypage/", False),
        ("/login", False),
        ("/login?next=/", False),
    ],
)
def test_the_longest_matching_rule_decides(path: str, allowed: bool) -> None:
    assert Robots(SITE, AGENT).allows("https://example.test" + path) is allowed


def test_a_named_group_wins_over_the_star_group() -> None:
    assert not Robots(SITE, "ClaudeBot/1.0").allows("https://example.test/works/a/vol/1")
    assert not Robots(SITE, "gptbot").allows("https://example.test/")  # names match any case


def test_ties_wildcards_ends_and_delays() -> None:
    rules = Robots(
        "User-agent: manga-atlas\n"
        "Disallow: /a\nAllow: /a/b\n"  # longer wins
        "Disallow: /same\nAllow: /same\n"  # a tie goes to Allow
        "Disallow: /*.gif$\n"
        "Disallow:\n"  # empty: no rule
        "Crawl-delay: 7\n",
        AGENT,
    )
    url = "https://example.test"
    assert not rules.allows(url + "/a/x") and rules.allows(url + "/a/b/c")
    assert rules.allows(url + "/same")
    assert not rules.allows(url + "/img/p.gif") and rules.allows(url + "/img/p.gif?x")
    assert rules.allows(url + "/other")
    assert rules.delay == 7.0


def test_groups_for_one_agent_are_combined_and_others_ignored() -> None:
    rules = Robots(
        "User-agent: *\nDisallow: /x\n\nUser-agent: other\nDisallow: /\n\nUser-agent: *\n"
        "Disallow: /y\n",
        AGENT,
    )
    assert not rules.allows("https://h/x") and not rules.allows("https://h/y")
    assert rules.allows("https://h/z")


def test_nothing_or_everything() -> None:
    assert Robots("", AGENT).allows("https://h/anything")
    assert Robots.everything(allowed=True).allows("https://h/a")
    assert not Robots.everything(allowed=False).allows("https://h/a")


def test_comments_blank_lines_and_other_records_do_not_split_a_group() -> None:
    rules = Robots(
        "User-agent: manga-atlas\n# us, and\n\nUser-agent: Other\nSitemap: /s.xml\nDisallow: /\n",
        AGENT,
    )
    assert not rules.allows("https://h/anything")  # the Disallow is ours too
