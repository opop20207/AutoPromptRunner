# Start the Vite frontend dev server (http://localhost:5173) on Windows.
#
#   ./scripts/dev_frontend.ps1
#
# It calls the API at http://localhost:8000 by default; override with VITE_API_BASE_URL.
# Start the backend first (./scripts/dev_api.ps1). Does not require admin rights.
$ErrorActionPreference = 'Stop'

$FrontendDir = Join-Path $PSScriptRoot '..\frontend'
Set-Location $FrontendDir
npm install
npm run dev
