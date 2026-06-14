#!/usr/bin/env bash
# Install the AutoPromptRunner Python package in editable mode (with the dev extra, which
# adds the optional FastAPI backend + test client). The CLI core itself needs no third-party
# packages. No external AI tools are invoked and neither Claude Code nor Codex is required.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== installing autoprompt-runner (editable, [dev] extra) =="
python -m pip install -e ".[dev]"

echo "== CLI =="
python -m autoprompt_runner.cli version || python -m autoprompt_runner.cli --help
