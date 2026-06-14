# Full local check suite for AutoPromptRunner on Windows: backend tests, config validation,
# a mock-only provider check, and the frontend build. Safe commands only -- this NEVER invokes
# Claude Code, Codex, or any external AI tool, and uses no network beyond the in-process
# FastAPI test client. Does not require admin rights and does not change the execution policy.
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$sep = [IO.Path]::PathSeparator
$env:PYTHONPATH = (Join-Path $Root 'src') + $(if ($env:PYTHONPATH) { $sep + $env:PYTHONPATH } else { '' })

Write-Host '== backend tests =='
python -c "import pytest"
if ($LASTEXITCODE -eq 0) { python -m pytest -q } else { python -m unittest discover -s tests -t tests }
if ($LASTEXITCODE -ne 0) { Write-Error 'backend tests failed'; exit 1 }

Write-Host '== config validate =='
python -m autoprompt_runner.cli config validate
if ($LASTEXITCODE -ne 0) { Write-Error 'config validation failed'; exit 1 }

Write-Host '== provider check (mock only) =='
# Throwaway database so this never touches the user's local state. Only the offline mock
# provider is checked -- Claude Code / Codex are never required or invoked.
$CheckDb = Join-Path $env:TEMP ('autoprompt_check_' + [guid]::NewGuid().ToString('N') + '.db')
python -m autoprompt_runner.cli provider seed --db-path $CheckDb | Out-Null
python -m autoprompt_runner.cli provider check --name mock --db-path $CheckDb
$providerExit = $LASTEXITCODE
if (Test-Path $CheckDb) { Remove-Item $CheckDb -Force }
if ($providerExit -ne 0) { Write-Error 'mock provider check failed'; exit 1 }

Write-Host '== frontend build =='
Push-Location (Join-Path $Root 'frontend')
if (-not (Test-Path 'node_modules')) { npm install }
npm run build
$buildExit = $LASTEXITCODE
Pop-Location
if ($buildExit -ne 0) { Write-Error 'frontend build failed'; exit 1 }

Write-Host 'All checks passed.'
