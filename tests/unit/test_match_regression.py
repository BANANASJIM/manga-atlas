"""Matcher regression on hand-labelled samples.

Each pair is (Bangumi work, one source's search results); results are labelled S (same work),
R (related work) or unlabelled (other). Precision and recall count accepted results.

- main.json: the design experiment's sample (38 series); the rules were tuned on it.
- heldout.json: 13 series labelled after the rules were frozen. The two fixes ('A (B)' titles,
  the Chinese half of a mixed alias) were written after seeing its two misses, so its recall
  is optimistic; without them it scores P 1.00 / R 0.93.
- fresh.json: 12 series drawn (seed 20261121) after the fixes were frozen; only each query's
  first five results, which is all the app compares.
"""

import json
from pathlib import Path

import pytest

from manga_atlas.config import Settings
from manga_atlas.match import Matcher, WorkNames

FIXTURES = Path(__file__).parent.parent / "fixtures" / "matching"


def score(name: str) -> tuple[float, float]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    matcher = Matcher(Settings())
    works = {w["id"]: w for w in data["works"]}
    tp = fp = fn = 0
    for pair in data["pairs"]:
        w = works[pair["work"]]
        names = WorkNames(
            w["id"], w["name"], w["name_cn"], tuple(w["aliases"]), tuple(w["authors"])
        )
        keys = matcher.work_keys(names)
        for _id, title, _author, label in pair["results"]:
            accepted = matcher.match_title(title, keys) is not None
            tp += accepted and label == "S"
            fp += accepted and label != "S"
            fn += not accepted and label == "S"
    return tp / (tp + fp), tp / (tp + fn)


@pytest.mark.parametrize(
    ("name", "min_precision", "min_recall"),
    [("main.json", 0.95, 0.93), ("heldout.json", 0.95, 0.90), ("fresh.json", 0.95, 0.90)],
)
def test_precision_and_recall(name: str, min_precision: float, min_recall: float) -> None:
    precision, recall = score(name)
    assert precision >= min_precision, f"{name}: P={precision:.3f}"
    assert recall >= min_recall, f"{name}: R={recall:.3f}"
