# Start the AutoPromptRunner FastAPI backend (local development) on Windows.
#
#   pip install -e ".[api]"
#   ./scripts/dev_api.ps1
#
# Host/port come from the config/env (AUTOPROMPT_API_HOST / AUTOPROMPT_API_PORT),
# defaulting to 127.0.0.1:8000. No external AI tools are invoked. Does not require admin
# rights and does not change the execution policy.
$ErrorActionPreference = 'Stop'

$ApiHost = if ($env:AUTOPROMPT_API_HOST) { $env:AUTOPROMPT_API_HOST } else { '127.0.0.1' }
$ApiPort = if ($env:AUTOPROMPT_API_PORT) { $env:AUTOPROMPT_API_PORT } else { '8000' }

python -m uvicorn autoprompt_runner.api.app:app --host $ApiHost --port $ApiPort --reload
