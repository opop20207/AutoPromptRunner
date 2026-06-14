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

echo "== provider check (mock only) =="
# Use a throwaway database so this never touches the user's local state. Only the offline
# mock provider is checked -- Claude Code / Codex are never required or invoked.
CHECK_DB="$(mktemp -t autoprompt_check_XXXXXX.db)"
python -m autoprompt_runner.cli provider seed --db-path "$CHECK_DB" >/dev/null
python -m autoprompt_runner.cli provider check --name mock --db-path "$CHECK_DB"
rm -f "$CHECK_DB"

echo "== frontend build =="
if [ -d frontend/node_modules ]; then
  ( cd frontend && npm run build )
else
  ( cd frontend && npm install && npm run build )
fi

echo "All checks passed."
