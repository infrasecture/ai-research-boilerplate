#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests scripts
uv run --locked ruff format --check src tests scripts
