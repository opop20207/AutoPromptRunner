#!/usr/bin/env bash
# Start the AutoPromptRunner FastAPI backend (local development).
#
#   pip install -e ".[api]"
#   scripts/dev_api.sh
#
# Host/port come from the config/env (AUTOPROMPT_API_HOST / AUTOPROMPT_API_PORT),
# defaulting to 127.0.0.1:8000. No external AI tools are invoked.
set -euo pipefail

HOST="${AUTOPROMPT_API_HOST:-127.0.0.1}"
PORT="${AUTOPROMPT_API_PORT:-8000}"

exec python -m uvicorn autoprompt_runner.api.app:app --host "$HOST" --port "$PORT" --reload
