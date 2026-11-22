"""Settings. Defaults live here; ~/.config/manga-atlas/config.toml may override any field."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / fallback


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    enabled: bool = True
    lang: str = "zh"  # "ja": search by the original (Japanese) name first
    # Its search results name authors, so it can tell when a listing holds the author's other
    # works too; only such a source's titles are searched on the others.
    lists_authors: bool = False


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 4568
    suwayomi_url: str = "http://127.0.0.1:4567"
    data_dir: Path = field(
        default_factory=lambda: _xdg("XDG_DATA_HOME", ".local/share") / "manga-atlas"
    )
    user_agent: str = "manga-atlas/0.1 (+https://github.com/BANANASJIM/manga-atlas)"
    archive_repo: str = "bangumi/Archive"

    # Availability: sources are checked in this order. The defaults are publishers' own sites;
    # config.toml may list others (config.example.toml shows how).
    sources: tuple[Source, ...] = (
        Source("5161357527629843269", "Shonen Jump+", lang="ja"),
        Source("5726629019734498294", "Tonari no Young Jump", lang="ja"),
        Source("583369548372015159", "Magazine Pocket", lang="ja"),
        Source("5332604811772266259", "Comic Days", lang="ja"),
        Source("8083066303045184316", "Sunday Web Every", lang="ja"),
        Source("7948822981386785924", "Manga One", lang="ja"),
        Source("8396089563187142394", "KadoComi", lang="ja"),
        Source("697820552173578283", "Manga UP!", lang="ja"),
        Source("3322701627784242064", "Young Jump+", lang="ja"),
        Source("2126618372106431410", "Young Magazine", lang="ja"),
        Source("1467355738916791140", "Gangan Online", lang="ja"),
        Source("2937751339618832603", "Pixiv Comic", lang="ja"),
        Source("1452408892924080686", "Niconico Seiga", lang="ja"),
        Source("3118962894631295374", "Comic Gardo", lang="ja"),
        Source("7341666123577520203", "COMIC FUZ", lang="ja"),
    )
    source_interval_s: float = 1.5
    source_timeout_s: float = 8.0
    breaker_failures: int = 2
    breaker_cooldown_s: float = 600.0
    cache_hit_ttl_days: int = 30
    cache_miss_ttl_days: int = 3
    match_fuzzy_threshold: float = 0.85
    match_short_title_len: int = 4

    # Catalogue build.
    tag_min_votes: int = 2
    standalone_min_collects: int = 10
    r18_user_tags: tuple[str, ...] = ("r18", "成年コミック")
    r18_min_votes: int = 2

    # Catalogue build, search and covers.
    archive_latest_url: str = "https://raw.githubusercontent.com/{repo}/master/aux/latest.json"
    download_timeout_s: float = 60.0
    tag_vocab_min_works: int = 20  # a user tag joins the vocabulary when this many works carry it
    tag_person_noise_share: float = 0.3  # ...unless this share of them are by a same-named person
    source_tag_min_works: int = 2  # a tag only another source gives joins it from this many works
    region_entity_min_works: int = 15  # a publisher/magazine predicts region from this many works
    region_entity_min_share: float = 0.9  # ...when this share of them agree
    score_min_votes: int = 20  # sorting by score puts works with fewer votes last
    search_limit: int = 50
    search_max_limit: int = 200
    item_authors_max: int = 3  # creators a result row names
    item_tags_max: int = 6  # tags a result row lists
    item_summary_chars: int = 160  # leading characters of the summary a result row carries
    suggest_limit: int = 8
    facet_tag_limit: int = 60
    tag_header_tags: int = 16
    tag_header_people: int = 8
    cover_url: str = "https://api.bgm.tv/v0/subjects/{id}/image?type=common"
    cover_placeholder: str = "/img/no_icon_subject.png"  # where Bangumi sends works without one
    cover_concurrency: int = 4
    cover_timeout_s: float = 10.0
    cover_miss_ttl_s: float = 3600.0

    # Supplementary tag sources beside Bangumi; each keeps its cache in data_dir/<source key>.
    wikidata_sparql_url: str = "https://query.wikidata.org/sparql"  # P5732 links and zh labels
    # Japanese Wikipedia: categories of the article matched by Japanese title.
    jawiki_enabled: bool = True
    jawiki_refresh_days: int = 30  # the build fetches anew when the cache is older
    jawiki_titles_url: str = (
        "https://dumps.wikimedia.org/jawiki/latest/jawiki-latest-all-titles-in-ns0.gz"
    )
    jawiki_site: str = "https://ja.wikipedia.org/"  # the site Wikidata links articles of
    jawiki_api_url: str = "https://ja.wikipedia.org/w/api.php"
    jawiki_article_url: str = "https://ja.wikipedia.org/wiki/{title}"
    jawiki_title_suffixes: tuple[str, ...] = (" (漫画)", " (漫画作品)")  # before the bare title
    jawiki_batch: int = 50  # titles per API request: the most the API takes from a non-bot
    jawiki_interval_s: float = 1.0  # between API requests, which are never concurrent
    jawiki_maxlag_s: int = 5  # the API refuses while its replicas lag more than this...
    jawiki_lag_retries: int = 5  # ...so wait as it asks (also on 429/503), this many times at most
    jawiki_busy_wait_s: float = 10.0  # a 429/503 without Retry-After: this long times the attempt
    jawiki_max_pages: int = 0  # categories of at most this many works, most collected first; 0: all
    # Chinese titles from Wikidata (the zh labels of items with a Bangumi ID), added as aliases.
    wikidata_zh_enabled: bool = True
    wikidata_zh_refresh_days: int = 30
    # ゆりみる: a yuri database's own tags, its manga matched by Japanese title.
    yurimiru_enabled: bool = True
    yurimiru_refresh_days: int = 30
    yurimiru_robots_url: str = "https://www.yurimiru.com/robots.txt"
    yurimiru_sitemap_url: str = "https://www.yurimiru.com/sitemap.xml"
    yurimiru_work_url: str = r"/works/([0-9a-f-]{36})/vol/1$"  # the sitemap's work pages; group: id
    yurimiru_media: tuple[str, ...] = ("漫画", "Web漫画")  # the site's media that are manga
    yurimiru_yuri_sweetness: int = 50  # 百合 also for works at least this sweet (糖度 %)
    yurimiru_interval_s: float = 3.0  # between requests, which are never concurrent
    yurimiru_busy_retries: int = 3  # on 429/503 wait (Retry-After, else busy_wait × attempt)...
    yurimiru_busy_wait_s: float = 30.0  # ...and try again
    yurimiru_redirects: int = 5  # followed at most, each only within the site and robots.txt
    yurimiru_save_every: int = 100  # pages between saves of what a long refresh has fetched
    yurimiru_max_pages: int = 0  # pages per refresh at most, most recently changed first; 0: all

    # Suwayomi access, matching rules, availability and library.
    suwayomi_timeout_s: float = 5.0  # calls that never reach a source (library, categories, ping)
    suwayomi_source_timeout_s: float = 30.0  # after our timeout Suwayomi may still be fetching
    source_empty_markers: tuple[str, ...] = ("漫画结果为空",)  # an error that means "0 hits"
    source_dead_markers: tuple[str, ...] = ("漫畫未找到", "漫画未找到")  # search hit, details gone
    source_no_chapters_markers: tuple[str, ...] = ("No chapters found",)  # an empty chapter list
    match_top_n: int = 5  # only the first N search results are compared
    match_details_max: int = 2  # detail calls per source to confirm an author (work page only)
    match_queries_max: int = 3  # names searched per Chinese source until one finds the work
    # Same work in another edition; matched after normalization, so any script works.
    match_edition_words: tuple[str, ...] = (
        "数码全彩", "全彩版", "彩色版", "全彩", "完全版", "新装版", "新装", "爱藏版", "典藏版",
        "豪华版", "精装版", "重制版", "完整版", "合集", "单行本", "修正版",
        "カラー版", "フルカラー", "合本版", "分冊版", "単話版",
    )  # fmt: skip
    # A trailing "(…)" containing one of these names a different work (side story, art book…).
    match_related_words: tuple[str, ...] = (
        "番外", "外传", "续", "前传", "后传", "公式", "画集", "设定", "同人", "精选", "官方",
        "周边", "学园", "剧场", "短篇", "第", "部", "季", "篇", "话",
        "短編", "編", "アンソロジー", "スピンオフ", "ファンブック",
    )  # fmt: skip
    check_batch_max: int = 100  # works per "检查本页" request
    app_request_timeout_s: float = 300.0  # warm → the app: each request, each progress line
    check_parallel_works: int = 3  # works a page check runs at once (work pages wait behind them)
    library_sync_ttl_s: float = 60.0

    @property
    def atlas_db(self) -> Path:
        return self.data_dir / "atlas.db"

    @property
    def state_db(self) -> Path:
        return self.data_dir / "state.db"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def dumps_dir(self) -> Path:
        return self.data_dir / "dumps"

    @property
    def build_lock(self) -> Path:
        return self.data_dir / "build.lock"

    def tag_cache(self, key: str) -> Path:
        """Folder where the tag source `key` keeps what it fetched."""
        return self.data_dir / key

    @property
    def enabled_sources(self) -> tuple[Source, ...]:
        return tuple(s for s in self.sources if s.enabled)


def config_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "manga-atlas" / "config.toml"


def load(path: Path | None = None) -> Settings:
    """Defaults, overridden by the TOML file when it exists. Unknown keys are an error."""
    path = path or config_path()
    if not path.exists():
        return Settings()
    with path.open("rb") as fh:
        raw: dict[str, Any] = tomllib.load(fh)
    known = {f.name for f in fields(Settings)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown settings in {path}: {sorted(unknown)}")
    kinds = get_type_hints(Settings)
    return Settings(**{key: _coerce(key, kinds[key], value) for key, value in raw.items()})


def _coerce(key: str, kind: Any, value: Any) -> Any:
    """A TOML value as the field's type: lists become tuples (of Source for `sources`)."""
    if get_origin(kind) is tuple:
        if not isinstance(value, list):
            raise ValueError(f"setting {key} must be a list")
        item = get_args(kind)[0]
        return tuple(Source(**v) if item is Source else v for v in value)
    if kind is Path:
        return Path(value).expanduser()
    return value
