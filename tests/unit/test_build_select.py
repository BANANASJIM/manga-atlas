"""Work selection: series plus popular standalone volumes, folding, adult exclusion, attributes."""

from manga_atlas.build import catalog, infobox
from manga_atlas.build import vocab as vocab_rules
from manga_atlas.build.catalog import Other, Subject, Tagger, Tally
from manga_atlas.build.schema import Kind, Status
from manga_atlas.config import Settings

SETTINGS = Settings(standalone_min_collects=10, r18_user_tags=("r18", "成年コミック"))


def subj(id: int, series: bool = True, **kw: object) -> Subject:
    base: dict[str, object] = {
        "id": id, "series": series, "nsfw": False, "name": f"s{id}", "name_cn": "", "date": "",
        "score": 0.0, "votes": 0, "rank": 0, "collects": 100, "tags": [], "meta": [],
        "infobox": "", "summary": "",
    }  # fmt: skip
    base.update(kw)
    return Subject(**base)  # type: ignore[arg-type]


def tagger(subjects: list[Subject]) -> Tagger:
    return Tagger.for_subjects(subjects, vocab_rules.load(), SETTINGS)


def test_relations_fold_volumes_into_the_lowest_series() -> None:
    comics = {s.id: s for s in [subj(1), subj(2), subj(10, False), subj(11, False)]}
    rows = [
        {"subject_id": 10, "relation_type": catalog.REL_SERIES, "related_subject_id": 2},
        {"subject_id": 10, "relation_type": catalog.REL_SERIES, "related_subject_id": 1},
        {"subject_id": 2, "relation_type": catalog.REL_VOLUME, "related_subject_id": 11},
        {"subject_id": 1, "relation_type": 1006, "related_subject_id": 2},
        {"subject_id": 99, "relation_type": 1006, "related_subject_id": 1},  # not manga
    ]
    parent, edges = catalog.read_relations(rows, comics)
    assert parent == {10: 1, 11: 2}
    assert edges == [(1, 1006, 2)]


def test_select_keeps_series_and_popular_standalones_and_drops_adult_works() -> None:
    subjects = [
        subj(1),
        subj(2, nsfw=True),
        subj(3, meta=["R18"]),
        subj(4, tags=[("R18", 2)]),
        subj(5, tags=[("R18", 1)]),
        subj(6, tags=[("成年コミック", 3)]),
        subj(7, tags=[("r18漫画", 2)]),  # folds into r18 through the suffix rule
        subj(20, False),  # volume of 1
        subj(21, False, collects=10),  # standalone, popular enough
        subj(22, False, collects=9),
        subj(23, False, collects=50, nsfw=True),
        subj(24, False),  # volume of adult series 2
    ]
    comics = {s.id: s for s in subjects}
    sel = catalog.select(comics, {20: 1, 24: 2}, tagger(subjects), SETTINGS)
    assert set(sel.works) == {1, 5, 21}
    assert sel.kinds[1] is Kind.SERIES and sel.kinds[21] is Kind.VOLUME
    assert sel.hidden == {2, 3, 4, 6, 7, 23, 24}
    assert [k.id for k in sel.children[1]] == [20]
    assert sel.stats["adult_series"] == 5 and sel.stats["adult_standalone"] == 1


def test_tally_folds_spellings_and_sums_votes() -> None:
    s = subj(1, tags=[("百合", 3), ("GL", 2), ("戀愛", 1), ("爱情", 1)], meta=["百合", "GL"])
    t = tagger([s]).tally(s)
    assert t.user == {"百合": 5, "恋爱": 2}
    assert t.official == ["百合"]


def test_relations_name_outside_subjects_and_skip_adult_ones() -> None:
    subjects = [subj(1), subj(2), subj(3, nsfw=True), subj(10, False), subj(11, False, collects=1)]
    comics = {s.id: s for s in subjects}
    sel = catalog.select(comics, {10: 1}, tagger(subjects), SETTINGS)
    others = {50: Other(2, "Anime", "动画", False), 51: Other(2, "Adult", "", True)}
    edges = [(10, 1, 50), (1, 1, 51), (1, 1006, 2), (2, 1005, 10), (1, 1099, 3), (1, 1010, 10)]
    edges += [(2, 1099, 11)]
    rows, targets = catalog.relations(edges, sel, comics, others, tagger(subjects))
    assert rows == {(1, 1, 50), (1, 1006, 2), (2, 1005, 1), (2, 1099, 11)}
    assert targets == {50: (2, "Anime", "动画"), 11: (catalog.BOOK, "s11", "")}


def test_attributes() -> None:
    v = vocab_rules.load()
    ended = infobox.parse("|结束= 2010年\n|册数= 既刊12卷(2020)")
    assert catalog.status_of(Tally({}, ["连载中"]), ended, v, 2) is Status.ONGOING
    assert catalog.status_of(Tally({}, []), ended, v, 2) is Status.COMPLETED
    assert catalog.status_of(Tally({"已完结": 2}, []), {}, v, 2) is Status.COMPLETED
    assert catalog.status_of(Tally({"已完结": 1}, []), {}, v, 2) is Status.UNKNOWN
    kids = [subj(10, False, date="2009-05-01"), subj(11, False, date="2008-01-02")]
    assert catalog.date_of(subj(1), kids) == "2008-01-02"
    assert catalog.date_of(subj(1, date="2001-01-01"), kids) == "2001-01-01"
    assert catalog.year_of(["", "2008-08-11（2008年37号）"]) == 2008
    assert catalog.year_of(["0000-00-00", "9999"]) is None
    assert catalog.vols_of(Kind.SERIES, kids, ended) == 12
    assert catalog.vols_of(Kind.SERIES, kids, infobox.parse("|册数= 二卷既刊（2024-05）")) == 2
    assert catalog.vols_of(Kind.VOLUME, [], ended) == 1
