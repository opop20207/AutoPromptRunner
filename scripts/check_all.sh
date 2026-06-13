#!/usr/bin/env bash
# Run the full local check suite for AutoPromptRunner: backend tests, config validation,
# and the frontend build. Safe commands only -- this NEVER invokes Claude Code, Codex, or
# any external AI tool, and uses no network beyond the in-process FastAPI test client.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

echo "== backend tests =="
if python -c "import pytest" >/dev/null 2>&1; then
  python -m pytest -q
else
  python -m unittest discover -s tests -t tests
fi

echo "== config validate =="
python -m autoprompt_runner.cli config validate

echo "== frontend build =="
if [ -d frontend/node_modules ]; then
  ( cd frontend && npm run build )
else
  ( cd frontend && npm install && npm run build )
fi

echo "All checks passed."
