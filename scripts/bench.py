"""Server-side latency of catalogue queries on the real atlas.db: p50 / p95 / max per class.

Times the query functions behind /api/search and /api/suggest in-process (warm caches), or the
whole HTTP handler with --http. Queries are sampled from the catalogue with a fixed seed.

    uv run python scripts/bench.py [--n 300] [--seed 20261120] [--http]
"""

from __future__ import annotations

import argparse
import math
import random
import re
import statistics
import sys
import time
from collections.abc import Callable
from typing import Any

from opencc import OpenCC

from manga_atlas.build.schema import counted
from manga_atlas.config import Settings, load
from manga_atlas.query.db import Handle, open_catalog
from manga_atlas.query.search import Query, search
from manga_atlas.query.suggest import suggest

SORTS = ("rank", "heat", "score", "date", "fav")
YEARS = (None, 1990, 2000, 2010, 2020)
TAG_MIN_WORKS = 150  # tags common enough to combine
_SEGMENT = re.compile(r"[^\s\W]{2,}")


def substring(rng: random.Random, title: str, k: int) -> str | None:
    """A k-character piece of one word of the title, as someone might type it."""
    words = [w for w in _SEGMENT.findall(title) if len(w) >= k]
    if not words:
        return None
    word = rng.choice(words)
    start = rng.randrange(len(word) - k + 1)
    return word[start : start + k]


def sample_titles(h: Handle, rng: random.Random, n: int, k: int) -> list[str]:
    titles = [t for (t,) in h.con.execute("SELECT name_cn FROM work WHERE length(name_cn) >= 4")]
    out: list[str] = []
    while len(out) < n:
        piece = substring(rng, rng.choice(titles), k)
        if piece:
            out.append(piece)
    return out


def build_cases(h: Handle, settings: Settings, rng: random.Random, n: int) -> dict[str, list[Any]]:
    s2t = OpenCC("s2t")
    simplified = [s for s in sample_titles(h, rng, n * 2, 3) + sample_titles(h, rng, n * 2, 4) if s]
    simplified = rng.sample(simplified, n)
    tags = [
        name
        for name, count in h.con.execute(
            "SELECT t.name, count(*) FROM work_tag wt JOIN tag t ON t.id = wt.tag_id"
            f" WHERE {counted('?', 'wt')} GROUP BY t.id",
            (settings.tag_min_votes,),
        )
        if count >= TAG_MIN_WORKS
    ]
    regions = [None, None, *h.catalog.regions[:3]]
    combos = []
    for _ in range(n):
        a, b, c = rng.sample(tags, 3)
        region = rng.choice(regions)
        combos.append(
            Query(
                include=(a, b),
                exclude=(c,),
                year_from=rng.choice(YEARS),
                regions=(region,) if region else (),
                sort=rng.choice(SORTS),
            )
        )
    people = [
        pid
        for (pid,) in h.con.execute(
            "SELECT person_id FROM work_person WHERE role <= 3 AND person_id > 0"
            " GROUP BY person_id HAVING count(*) >= 2"
        )
    ]
    names = [
        name
        for (name,) in h.con.execute(
            "SELECT name FROM person WHERE id > 0 AND works >= 2 AND length(name) >= 3"
        )
    ]
    return {
        "title, Simplified, 3-4 chars": [Query(text=s) for s in simplified],
        "title, Traditional, 3-4 chars": [Query(text=str(s2t.convert(s))) for s in simplified],
        "title, 2 chars (LIKE scan)": [Query(text=s) for s in sample_titles(h, rng, n, 2)],
        "tags: 2 AND + 1 NOT + filters + sort": combos,
        "person: works of an author": [
            Query(people=(pid,), sort=rng.choice(SORTS)) for pid in rng.sample(people, n)
        ],
        "suggest: author name, 3 chars": [
            ("suggest", f"作者:{name[:3]}") for name in rng.sample(names, n)
        ],
        "suggest: title, 2-4 chars": [
            ("suggest", s)
            for s in rng.sample(simplified, n // 2) + sample_titles(h, rng, n // 2, 2)
        ],
        "single filter: region / year / status": [
            rng.choice(
                [
                    Query(regions=(rng.choice(h.catalog.regions[:2]),)),
                    Query(year_from=rng.choice(YEARS[1:])),
                    Query(statuses=("已完结",)),
                    Query(exclude=(rng.choice(tags),)),
                ]
            )
            for _ in range(n)
        ],
        "home: everything, each sort": [Query(sort=rng.choice(SORTS)) for _ in range(n)],
    }


def runner(settings: Settings, http: bool) -> Callable[[Any], int]:
    if http:
        from fastapi.testclient import TestClient

        from manga_atlas.web.app import create_app

        client = TestClient(
            create_app(settings), base_url=f"http://{settings.host}:{settings.port}"
        )

        def via_http(case: Any) -> int:
            if isinstance(case, tuple):
                return len(client.get("/api/suggest", params={"q": case[1]}).content)
            params = {
                "q": case.text,
                "t": ",".join(case.include),
                "x": ",".join(case.exclude),
                "a": ",".join(map(str, case.people)),
                "y": "" if case.year_from is None else f"{case.year_from}-",
                "st": ",".join(case.statuses),
                "region": ",".join(case.regions),
                "sort": case.sort,
            }
            return len(client.get("/api/search", params=params).content)

        return via_http

    def direct(case: Any) -> int:
        h = open_catalog(settings)
        if isinstance(case, tuple):
            return sum(len(group) for group in suggest(h, settings, case[1]).values())
        return int(search(h, settings, case)["total"])

    return direct


def percentile(sorted_ms: list[float], p: float) -> float:
    return sorted_ms[max(0, math.ceil(p * len(sorted_ms)) - 1)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--n", type=int, default=300, help="queries per class")
    parser.add_argument("--seed", type=int, default=20261120)
    parser.add_argument("--http", action="store_true", help="time the HTTP handlers too")
    args = parser.parse_args(argv)
    settings = load()
    h = open_catalog(settings)
    rng = random.Random(args.seed)
    cases = build_cases(h, settings, rng, args.n)
    run = runner(settings, args.http)
    print(
        f"{settings.atlas_db}  works={h.catalog.works:,}  mode={'http' if args.http else 'query'}"
    )
    print(f"{'class':40s} {'n':>5s} {'p50 ms':>8s} {'p95 ms':>8s} {'max ms':>8s} {'non-empty':>9s}")
    worst = 0.0
    for name, batch in cases.items():
        for case in batch[:10]:  # warm the page cache and per-connection caches
            run(case)
        times, hits = [], 0
        for case in batch:
            start = time.perf_counter()
            hits += bool(run(case))
            times.append((time.perf_counter() - start) * 1000)
        times.sort()
        p95 = percentile(times, 0.95)
        worst = max(worst, p95)
        print(
            f"{name:40s} {len(times):5d} {statistics.median(times):8.2f} {p95:8.2f}"
            f" {times[-1]:8.2f} {hits / len(times):9.0%}"
        )
    print(f"worst p95: {worst:.2f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
