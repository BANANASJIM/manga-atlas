"""Chinese titles from Wikidata: the zh labels of manga items that carry a Bangumi subject ID.

refresh(): when the cache is older than wikidata_zh_refresh_days, one SPARQL query; a failure keeps
the old cache. names(): work id -> labels, which the build adds to the work's aliases, so search,
matching and the work page know them. Only labels (one per Chinese locale): an item's aliases
(skos:altLabel) also name its side stories and arcs, which as aliases would make the matcher take
a side story for the work.
"""

from __future__ import annotations

from pathlib import Path

from manga_atlas.build.tagsources import common
from manga_atlas.build.tagsources.common import Log
from manga_atlas.config import Settings

KEY = "wikidata"  # its folder under data_dir, beside the tag sources'
CACHE = "zh-labels.json"
# Manga series (Q21198342) and manga (Q8274) items with a Bangumi subject ID (P5732).
_SPARQL = """SELECT ?bangumi ?label WHERE {
  VALUES ?kind { wd:Q21198342 wd:Q8274 }
  ?item wdt:P31 ?kind ; wdt:P5732 ?bangumi ; rdfs:label ?label .
  FILTER(STRSTARTS(LANG(?label), "zh"))
}"""


def _cache(settings: Settings) -> Path:
    return settings.tag_cache(KEY) / CACHE


def refresh(settings: Settings, log: Log) -> None:
    if not settings.wikidata_zh_enabled:
        return
    cache = _cache(settings)
    old = common.read(cache, log)
    if old is not None and common.fresh(old, settings.wikidata_zh_refresh_days):
        return
    try:
        with common.client(settings) as client:
            rows = common.sparql(client, settings, _SPARQL)
        labels: dict[str, list[str]] = {}
        for row in rows:
            wid, label = row["bangumi"]["value"], row["label"]["value"].strip()
            if wid.isdigit() and label and label not in labels.setdefault(wid, []):
                labels[wid].append(label)
        common.write(cache, {"labels": labels})
    except Exception as e:  # a supplementary source never fails the Bangumi build
        log(f"wikidata: fetch failed, keeping the old cache ({type(e).__name__}: {e})")
        return
    log(f"wikidata: Chinese labels for {len(labels):,} Bangumi subjects")


def names(settings: Settings, log: Log) -> dict[int, list[str]]:
    if not settings.wikidata_zh_enabled:
        return {}
    data = common.read(_cache(settings), log)
    if data is None:
        return {}
    try:
        return {int(w): [str(t) for t in ts] for w, ts in data["labels"].items()}
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        log(f"wikidata: cache unusable, no labels from it ({type(e).__name__}: {e})")
        return {}
