# 配置

[English](configuration.md) | **简体中文**

MangaAtlas 没有配置文件也能运行。想改动什么，就创建 `~/.config/manga-atlas/config.toml`（遵循 `$XDG_CONFIG_HOME`），只写你要改的项。[`src/manga_atlas/config.py`](../src/manga_atlas/config.py) 中 `Settings` 的每个字段都可以在配置文件里设置，名称、默认值和各项的作用都以该文件为准。未知的键会报错；列表会替换默认列表，而不是追加到它后面。

[`config.example.toml`](../config.example.toml) 可以作为起点。

## 常用设置

| 设置项 | 作用 |
| --- | --- |
| `port`、`host` | Web 应用监听的地址（`127.0.0.1:4568`）。`host` 请保持为回环地址：没有登录认证。 |
| `suwayomi_url` | 你的 Suwayomi 服务器（`http://127.0.0.1:4567`）。 |
| `data_dir` | 存放目录、缓存和 `state.db` 的位置。 |
| `sources` | 要检查哪些 Suwayomi 源，按顺序排列（见下文）。 |
| `jawiki_enabled`、`wikidata_zh_enabled`、`yurimiru_enabled` | 关闭额外的标签和标题来源；不用日文维基百科和 ゆりみる，首次构建会快得多。 |
| `source_interval_s`、`source_timeout_s` | 对同一个源的两次请求之间的间隔，以及单次请求最长等待多久。 |
| `cache_hit_ttl_days`、`cache_miss_ttl_days` | 找到的或没找到的答案各自被信任多久。 |

## 漫画源

`sources` 的每一项是一个表：

```toml
[[sources]]
id = "5161357527629843269"   # Suwayomi 的源 id
name = "Shonen Jump+"        # 显示在作品页上
lang = "ja"                  # "ja"：按原名搜索；"zh"（默认）：中文源
enabled = true               # false 会保留这一项，但从不去询问该源
lists_authors = false        # 如果它的搜索结果带有作者名，设为 true
```

默认值是各出版社自家的日文网站。设置 `sources` 会替换这份默认列表，所以要把你想用的源全部列出来；`config.example.toml` 列出了默认的源，并示范了中文源怎么写。

要找到某个源的 id，可以向你的 Suwayomi 服务器查询你已安装的源：

```sh
curl -s http://127.0.0.1:4567/api/graphql -H 'Content-Type: application/json' \
  -d '{"query": "{ sources { nodes { id name lang } } }"}'
```

`lang` 请用 `ja` 或 `zh`。其他值会按中文搜索，但没有其余的中文处理：「检查本页」先问中文源、另一种译名、`lists_authors`。

`lists_authors` 只对中文源有意义：如果某个源的搜索结果带有作者，MangaAtlas 就能区分「以另一种译名列出的作品」和「该作者的其他作品」，然后把这个标题也拿到其他中文源上搜索。

## 用克隆的仓库运行服务

`deploy/` 里的 unit 文件运行的是 `~/.local/bin/manga-atlas`，它由 `uv tool install .` 放在那里。如果想改为运行开发用的副本，就让 unit 指向它的虚拟环境：

```sh
systemctl --user edit manga-atlas.service
# [Service]
# ExecStart=
# ExecStart=/path/to/manga-atlas/.venv/bin/manga-atlas serve
```

`manga-atlas-build.service` 同理，把命令换成 `build`。
