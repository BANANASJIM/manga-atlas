# Configuration

**English** | [简体中文](configuration.zh-CN.md)

MangaAtlas runs without a config file. To change something, create
`~/.config/manga-atlas/config.toml` (`$XDG_CONFIG_HOME` is respected) and set only what you change.
Every field of `Settings` in [`src/manga_atlas/config.py`](../src/manga_atlas/config.py) can be
set there, and that file is the reference for names, defaults and what each one does. An unknown
key is an error, and a list replaces the default list rather than adding to it.

[`config.example.toml`](../config.example.toml) is a starting point.

## Common settings

| Setting | What it does |
| --- | --- |
| `port`, `host` | Where the web app listens (`127.0.0.1:4568`). Keep `host` on the loopback address: there is no authentication. |
| `suwayomi_url` | Your Suwayomi server (`http://127.0.0.1:4567`). |
| `data_dir` | Where the catalogue, caches and `state.db` live. |
| `sources` | Which Suwayomi sources to check, in order (below). |
| `jawiki_enabled`, `wikidata_zh_enabled`, `yurimiru_enabled` | Turn the extra tag and title sources off; the first build is much faster without Japanese Wikipedia and ゆりみる. |
| `source_interval_s`, `source_timeout_s` | The pause between two requests to one source, and how long to wait for one. |
| `cache_hit_ttl_days`, `cache_miss_ttl_days` | How long a found or not-found answer is trusted. |

## Sources

Each entry of `sources` is a table:

```toml
[[sources]]
id = "5161357527629843269"   # the Suwayomi source id
name = "Shonen Jump+"        # shown on the work page
lang = "ja"                  # "ja": search by the original title; "zh" (the default): Chinese
enabled = true               # false keeps the entry but never asks the source
lists_authors = false        # true if its search results name authors
```

The defaults are the publishers' own Japanese sites. Setting `sources` replaces that list, so list
every source you want; `config.example.toml` lists the defaults and shows where a Chinese source goes.

To find a source's id, ask your Suwayomi server for the sources you have installed:

```sh
curl -s http://127.0.0.1:4567/api/graphql -H 'Content-Type: application/json' \
  -d '{"query": "{ sources { nodes { id name lang } } }"}'
```

Use `ja` or `zh` for `lang`. Any other value gets Chinese searches but not the rest of the Chinese
handling: asking Chinese sources first on 检查本页, other translations, and `lists_authors`.

`lists_authors` matters only for Chinese sources: when a source's search results carry authors,
MangaAtlas can tell a work listed under another translation from the author's other works, and
then searches that title on the other Chinese sources too.

## Running from a clone as a service

The units in `deploy/` run `~/.local/bin/manga-atlas`, which `uv tool install .` puts there. To run
a development copy instead, point the units at its virtual environment:

```sh
systemctl --user edit manga-atlas.service
# [Service]
# ExecStart=
# ExecStart=/path/to/manga-atlas/.venv/bin/manga-atlas serve
```

Do the same for `manga-atlas-build.service` with `build`.
