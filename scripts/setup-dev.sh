#!/usr/bin/env bash
# One-time setup for a development checkout.
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --locked
git config core.hooksPath .githooks
echo "dev environment ready; pre-commit hook enabled"
