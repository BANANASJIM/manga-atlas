#!/usr/bin/env bash
# Quality gate: lint, format, types, tests (live and e2e tests are opt-in via -m).
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
