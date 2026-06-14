#!/usr/bin/env bash
# One-command local setup for AutoPromptRunner:
#   * create a Python virtual environment (.venv) if missing
#   * install the backend package (editable, [dev] extra)
#   * install the frontend npm dependencies
#   * create .autoprompt/config.toml if missing (config init)
#   * seed the built-in prompt templates and provider profiles
#
# Usage:
#   scripts/setup_local.sh            # safe: never overwrites an existing config
#   scripts/setup_local.sh --force    # also overwrite an existing config (config init --force)
#
# Neither Claude Code nor Codex is required, and no external AI tool is invoked.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FORCE=0
for arg in "$@"; do
  if [ "$arg" = "--force" ]; then FORCE=1; fi
done

# 1. Virtual environment.
if [ ! -d .venv ]; then
  echo "== creating virtual environment (.venv) =="
  python -m venv .venv
fi
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then
  # shellcheck disable=SC1091
  source .venv/Scripts/activate
else
  echo "error: could not find the virtual environment activate script" >&2
  exit 1
fi

# 2. Backend (editable + dev extra).
echo "== installing backend (editable, [dev]) =="
python -m pip install --upgrade pip >/dev/null
python -m pip install -e ".[dev]"

# 3. Frontend dependencies.
echo "== installing frontend dependencies =="
( cd frontend && npm install )

# 4. Config (never overwrite without --force).
echo "== config =="
if [ "$FORCE" -eq 1 ]; then
  python -m autoprompt_runner.cli config init --force
else
  python -m autoprompt_runner.cli config init || echo "  (config already exists; re-run with --force to overwrite)"
fi

# 5. Seed templates and provider profiles (idempotent; safe to re-run).
echo "== seeding templates and provider profiles =="
python -m autoprompt_runner.cli template seed || true
python -m autoprompt_runner.cli provider seed || true

cat <<'EOF'

Setup complete. Next steps (activate the venv first):
  source .venv/bin/activate        # or: source .venv/Scripts/activate (Windows Git Bash)
  scripts/dev_api.sh               # start the HTTP API   (http://127.0.0.1:8000)
  scripts/dev_worker.sh            # start the queue worker
  scripts/dev_frontend.sh          # start the web UI      (http://localhost:5173)
  python -m autoprompt_runner.cli --help
EOF
