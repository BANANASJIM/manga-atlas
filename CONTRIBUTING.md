# Contributing

**English** | [简体中文](CONTRIBUTING.zh-CN.md)

Thanks for helping. Issues and pull requests are welcome; for anything larger than a fix, open an
issue first so we can agree on the shape.

## Set up

```sh
uv sync --all-groups
uv run playwright install chromium   # only for the browser tests
```

You do not need Suwayomi or the network for the tests. To try the app itself, follow the
[quick start](README.md#quick-start).

## Checks

```sh
scripts/check.sh                     # ruff, formatting, strict mypy, unit and integration tests
uv run pytest -m e2e tests/e2e       # browser tests against a fake backend
```

Both must pass before a pull request is merged. `uv run pytest -m live` talks to a real Suwayomi
and the sources behind it; run it rarely, and keep live tests few.

## Ground rules

- **Keep it simple.** One user, one machine, one process and SQLite. No extra services, accounts or
  job queues.
- **No magic values.** Ports, URLs, source ids, thresholds and TTLs come from `config.py`. UI copy
  comes from `web/static/js/words.js` and colours and sizes from the CSS tokens. UI copy is short
  Chinese, one line per element.
- **One door to Suwayomi.** Only `suwayomi/` talks to it. Nothing adds to the library or writes
  categories except an explicit 整理书架 apply.
- **Be polite to sources.** Never two requests to one source at once; keep the spacing from
  `config.py`. Some sites ban an IP after a burst.
- **Matching changes need evidence.** Rules exist because of measured failures. Add the case to
  `tests/fixtures/matching` or a unit test, and keep the regression tests passing.
- **Comments only where they carry weight.**

## Commits and pull requests

- Commit messages in English, imperative mood, saying what changes; no AI attribution or
  `Co-Authored-By` lines.
- Keep a pull request to one change, and say why it is needed. Add screenshots for UI changes.

## Reporting bugs

Use the issue templates. For a security problem, follow [SECURITY.md](SECURITY.md) instead.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
