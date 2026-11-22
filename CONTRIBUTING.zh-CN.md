# 贡献指南

[English](CONTRIBUTING.md) | **简体中文**

感谢你的帮助。欢迎提交 issue 和 pull request；如果改动比一次修复更大，请先开一个 issue，我们先商量好大致方案。

## 搭建环境

```sh
uv sync --all-groups
uv run playwright install chromium   # 仅浏览器测试需要
```

跑测试不需要 Suwayomi，也不需要联网。想试用应用本身，请按[快速开始](README.zh-CN.md#快速开始)操作。

## 检查

```sh
scripts/check.sh                     # ruff、格式检查、严格模式的 mypy、单元测试和集成测试
uv run pytest -m e2e tests/e2e       # 针对模拟后端的浏览器测试
```

合并 pull request 之前，这两项都必须通过。`uv run pytest -m live` 会访问真实的 Suwayomi 及其背后的源；请少跑，并且少写 live 测试。

## 基本规则

- **保持简单。** 一个用户、一台机器、一个进程加 SQLite。不引入额外的服务、账号或任务队列。
- **不用魔法值。** 端口、URL、源 id、阈值和 TTL 都来自 `config.py`。界面文案来自 `web/static/js/words.js`，颜色和尺寸来自 CSS token。界面文案用简短的中文，每个元素一行。
- **Suwayomi 只有一个入口。** 只有 `suwayomi/` 与它通信。除了「整理书架」里明确的「应用」操作，任何地方都不会往书架里添加作品或写入分类。
- **对源要礼貌。** 绝不同时向同一个源发出两个请求；保持 `config.py` 里设定的间隔。有些网站在一阵密集请求之后会封 IP。
- **改匹配规则需要证据。** 每条规则都是因为实测中出现的失败才有的。请把对应的用例加到 `tests/fixtures/matching` 或单元测试里，并保持回归测试通过。
- **注释只留承重的。**

## 提交与 pull request

- 提交信息用英文、祈使语气，写明改了什么；不要加 AI 署名或 `Co-Authored-By` 行。
- 一个 pull request 只包含一项改动，并说明为什么需要它。界面改动请附上截图。

## 报告 Bug

请使用 issue 模板。安全问题请改为遵循 [SECURITY.md](SECURITY.md#安全)。

提交贡献即表示你同意，你的贡献以 [MIT 许可证](LICENSE)授权。
