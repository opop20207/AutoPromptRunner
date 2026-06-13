#!/usr/bin/env bash
# Start the local background queue worker (executes queued runs).
#
#   scripts/dev_worker.sh                 # poll forever (Ctrl+C to stop)
#   scripts/dev_worker.sh --once          # execute one queued job, then exit
#
# Extra flags (e.g. --once, --poll-interval-seconds, --config, --db-path) are forwarded.
set -euo pipefail

exec python -m autoprompt_runner.cli worker run "$@"
