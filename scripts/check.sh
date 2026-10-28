#!/usr/bin/env bash
# The single quality gate. CI runs exactly this; run it before every commit.
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --locked --quiet
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
