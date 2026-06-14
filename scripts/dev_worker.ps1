# Start the local background queue worker (executes queued runs) on Windows.
#
#   ./scripts/dev_worker.ps1                 # poll forever (Ctrl+C to stop)
#   ./scripts/dev_worker.ps1 --once          # execute one queued job, then exit
#
# Extra flags (e.g. --once, --poll-interval-seconds, --config, --db-path) are forwarded.
# No external AI tools are invoked; does not require admin rights.
$ErrorActionPreference = 'Stop'

python -m autoprompt_runner.cli worker run @args
