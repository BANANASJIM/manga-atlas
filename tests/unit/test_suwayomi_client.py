import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest

from manga_atlas.suwayomi import (
    GraphQLError,
    MangaGone,
    MangaState,
    SourceFailure,
    SourceTimeout,
    SuwayomiClient,
    SuwayomiUnavailable,
    manga_url,
)

Handler = Callable[[dict[str, Any]], httpx.Response]


def client(handler: Handler) -> SuwayomiClient:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/graphql"
        return handler(json.loads(request.content))

    return SuwayomiClient(
        "http://suwayomi.test/",
        timeout=1,
        source_timeout=1,
        empty_markers=("漫画结果为空",),
        dead_markers=("漫畫未找到",),
        no_chapters_markers=("No chapters found",),
        transport=httpx.MockTransport(respond),
    )


def data(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"data": payload})


def error(message: str) -> httpx.Response:
    return httpx.Response(200, json={"errors": [{"message": message}], "data": None})


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def test_search_parses_results_in_order() -> None:
    def handler(body: dict[str, Any]) -> httpx.Response:
        assert body["variables"]["input"] == {
            "source": "42",
            "type": "SEARCH",
            "page": 1,
            "query": "乒乓",
        }
        mangas = [
            {
                "id": 7,
                "title": "乒乓",
                "author": "松本大洋",
                "artist": None,
                "sourceId": "42",
            },
            {
                "id": 8,
                "title": "乒乓告白",
                "author": None,
                "artist": None,
                "sourceId": "42",
            },
        ]
        return data({"fetchSourceManga": {"mangas": mangas}})

    found = run(client(handler).search("42", "乒乓"))
    assert [(m.id, m.title, m.creators) for m in found] == [
        (7, "乒乓", "松本大洋"),
        (8, "乒乓告白", None),
    ]


def test_search_empty_marker_means_no_hits() -> None:
    assert run(client(lambda _: error("漫画结果为空")).search("1", "x")) == []


def test_search_errors_are_source_failures() -> None:
    msg = "Exception while fetching data (/fetchSourceManga) : Connect timed out\r\n\r\njava.io..."
    with pytest.raises(SourceFailure, match=r"^Connect timed out$"):
        run(client(lambda _: error(msg)).search("1", "x"))


def test_details_dead_entry_and_fields() -> None:
    def handler(body: dict[str, Any]) -> httpx.Response:
        assert body["variables"]["input"] == {"id": 5, "fetchManga": True, "fetchChapters": True}
        manga = {
            "id": 5,
            "title": "t",
            "author": "a",
            "artist": "b",
            "sourceId": "1",
        }
        return data({"fetchMangaAndChapters": {"manga": manga}})

    assert run(client(handler).manga(5)).creators == "a, b"
    with pytest.raises(MangaGone):
        run(client(lambda _: error("漫畫未找到!")).manga(5))


def test_unreachable_and_timeouts() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def make(transport: Callable[[httpx.Request], httpx.Response]) -> SuwayomiClient:
        return SuwayomiClient(
            "http://x", timeout=1, source_timeout=1, transport=httpx.MockTransport(transport)
        )

    with pytest.raises(SuwayomiUnavailable):
        run(make(refuse).search("1", "q"))
    with pytest.raises(SourceTimeout):
        run(make(slow).search("1", "q"))
    with pytest.raises(SuwayomiUnavailable):  # Suwayomi's own database being slow is not a source
        run(make(slow).library())
    with pytest.raises(SuwayomiUnavailable):
        run(client(lambda _: httpx.Response(502, text="bad gateway")).ping())
    with pytest.raises(SourceTimeout):
        run(make(slow).fetch_chapters(1))
    with pytest.raises(SuwayomiUnavailable):
        run(make(refuse).fetch_chapters(1))
    with pytest.raises(SuwayomiUnavailable):
        run(make(slow).manga_state(1))


def test_manga_state_reads_suwayomis_own_database() -> None:
    def handler(body: dict[str, Any]) -> httpx.Response:
        assert body["query"].startswith("query ")  # no mutation: nothing is fetched
        assert body["variables"] == {"id": 9}
        manga = {"sourceId": "42", "initialized": True, "chapters": {"totalCount": 0}}
        return data({"manga": manga})

    assert run(client(handler).manga_state(9)) == MangaState("42", True, 0)
    with pytest.raises(GraphQLError):  # an id Suwayomi does not know
        run(client(lambda _: error("manga not found")).manga_state(9))


def test_fetch_chapters_counts_them_and_waits_for_the_source() -> None:
    read_timeouts: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        read_timeouts.append(request.extensions["timeout"]["read"])
        body = json.loads(request.content)
        if "FetchChapters" in body["query"]:
            assert body["variables"] == {"input": {"mangaId": 5}}
            return data({"fetchChapters": {"chapters": [{"id": 1}, {"id": 2}, {"id": 3}]}})
        manga = {"sourceId": "42", "initialized": True, "chapters": {"totalCount": 0}}
        return data({"manga": manga})

    c = SuwayomiClient(
        "http://x", timeout=1, source_timeout=7, transport=httpx.MockTransport(respond)
    )
    assert run(c.manga_state(5)).chapter_count == 0
    assert run(c.fetch_chapters(5)) == 3
    assert read_timeouts == [1, 7]  # the database answers quickly; the source gets longer


def test_fetch_chapters_errors_are_the_entrys_source() -> None:
    msg = "Exception while fetching data (/fetchChapters) : Connect timed out\r\n\r\njava.io..."
    with pytest.raises(SourceFailure, match=r"^Connect timed out$"):
        run(client(lambda _: error(msg)).fetch_chapters(5))
    with pytest.raises(MangaGone):  # the entry's page is gone: the source itself is fine
        run(client(lambda _: error("漫畫未找到!")).fetch_chapters(5))


def test_no_chapters_listed_is_an_answer_not_a_failure() -> None:
    msg = "Exception while fetching data (/fetchChapters) : No chapters found\r\n\r\njava..."
    assert run(client(lambda _: error(msg)).fetch_chapters(5)) == 0


def test_details_of_an_entry_without_chapters_are_asked_alone() -> None:
    asked: list[bool] = []
    path = "/fetchMangaAndChapters"

    def handler(body: dict[str, Any]) -> httpx.Response:
        with_chapters = body["variables"]["input"]["fetchChapters"]
        asked.append(with_chapters)
        if with_chapters:
            return error(f"Exception while fetching data ({path}) : No chapters found")
        manga = {"id": 5, "title": "t", "author": "a", "sourceId": "1"}
        return data({"fetchMangaAndChapters": {"manga": manga}})

    assert run(client(handler).manga(5)).creators == "a"
    assert asked == [True, False]


def test_manga_url_is_where_the_web_ui_shows_an_entry() -> None:
    assert manga_url("http://suwayomi.test:4567", 12) == "http://suwayomi.test:4567/manga/12"
    assert manga_url("http://suwayomi.test:4567//", 12) == "http://suwayomi.test:4567/manga/12"


def test_library_and_categories() -> None:
    def handler(body: dict[str, Any]) -> httpx.Response:
        if "Library" in body["query"]:
            node = {"id": 3, "title": "魔男伊奇", "author": "西修", "artist": None, "sourceId": "9",
                    "categories": {"nodes": [{"id": 2}]}}  # fmt: skip
            return data({"mangas": {"nodes": [node]}})
        return data(
            {"categories": {"nodes": [{"id": 0, "name": "Default"}, {"id": 2, "name": "百合"}]}}
        )

    c = client(handler)
    [entry] = run(c.library())
    assert (entry.id, entry.title, entry.source_id, entry.category_ids) == (
        3,
        "魔男伊奇",
        "9",
        (2,),
    )
    assert [cat.name for cat in run(c.categories())] == ["Default", "百合"]


def test_category_writes_only_add() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(body: dict[str, Any]) -> httpx.Response:
        bodies.append(body)
        if "createCategory" in body["query"]:
            return data(
                {
                    "createCategory": {
                        "category": {"id": 4, "name": body["variables"]["input"]["name"]}
                    }
                }
            )
        return data({"updateMangasCategories": {"mangas": []}})

    c = client(handler)
    assert run(c.create_category("恐怖")).id == 4
    run(c.add_to_category(4, [9, 8, 9]))
    run(c.add_to_category(4, []))  # nothing to do: no request
    assert len(bodies) == 2
    assert bodies[1]["variables"]["input"] == {"ids": [8, 9], "patch": {"addToCategories": [4]}}


def test_source_exists_and_ping() -> None:
    def handler(body: dict[str, Any]) -> httpx.Response:
        if "Source" in body["query"]:
            return (
                data({"source": {"id": "1"}}) if body["variables"]["id"] == "1" else error("null")
            )
        return data({"aboutServer": {"version": "v2.4"}})

    c = client(handler)
    assert run(c.source_exists("1")) and not run(c.source_exists("2"))
    assert run(c.ping()) == "v2.4"
