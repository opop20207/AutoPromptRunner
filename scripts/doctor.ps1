# Environment diagnostics for AutoPromptRunner on Windows. Prints a compact report and exits
# non-zero only when a REQUIRED check fails (Python, CLI import, SQLite). Optional checks
# (Node/npm, config validity, frontend deps, and the claude / codex provider commands) only
# warn -- a missing external agent never fails this script. No external AI tool is invoked,
# no admin rights are needed, and the execution policy is not changed.
$ErrorActionPreference = 'Continue'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$sep = [IO.Path]::PathSeparator
$env:PYTHONPATH = (Join-Path $Root 'src') + $(if ($env:PYTHONPATH) { $sep + $env:PYTHONPATH } else { '' })

$script:fails = 0
$script:warns = 0
function Show-Pass($m) { Write-Host ('  [ok]   ' + $m) }
function Show-Warn($m) { Write-Host ('  [warn] ' + $m); $script:warns++ }
function Show-Fail($m) { Write-Host ('  [FAIL] ' + $m); $script:fails++ }

Write-Host 'AutoPromptRunner doctor'

# -- required --------------------------------------------------------------
$pyVer = (python --version)
if ($LASTEXITCODE -eq 0) { Show-Pass ('python: ' + $pyVer) } else { Show-Fail 'python: not found on PATH' }

$sqliteVer = (python -c "import sqlite3; print(sqlite3.sqlite_version)")
if ($LASTEXITCODE -eq 0) { Show-Pass ('sqlite (via python): ' + $sqliteVer) } else { Show-Fail 'sqlite: Python sqlite3 module unavailable' }

$cliVer = (python -m autoprompt_runner.cli version)
if ($LASTEXITCODE -eq 0) { Show-Pass ('autoprompt-runner CLI: ' + $cliVer) } else { Show-Fail 'autoprompt-runner CLI: import/run failed (is the package installed or src on PYTHONPATH?)' }

# -- optional --------------------------------------------------------------
if (Get-Command node -ErrorAction SilentlyContinue) { Show-Pass ('node: ' + (node --version)) } else { Show-Warn 'node: not found (needed only to build/run the web UI)' }
if (Get-Command npm -ErrorAction SilentlyContinue) { Show-Pass ('npm: ' + (npm --version)) } else { Show-Warn 'npm: not found (needed only to build/run the web UI)' }
if (Get-Command git -ErrorAction SilentlyContinue) { Show-Pass ('git: ' + (git --version)) } else { Show-Warn 'git: not found (needed for worktrees, checkpoints, and commits)' }

if (Test-Path (Join-Path $Root 'frontend\node_modules')) { Show-Pass 'frontend dependencies: installed' } else { Show-Warn 'frontend dependencies: missing (run npm install in frontend/)' }

python -m autoprompt_runner.cli config validate | Out-Null
if ($LASTEXITCODE -eq 0) { Show-Pass 'config: valid' } else { Show-Warn 'config: not initialized or invalid (autoprompt-runner config init / config validate)' }

# Optional provider commands -- never fail the doctor on these.
foreach ($cmd in @('claude', 'codex')) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { Show-Pass ("provider command '" + $cmd + "': found") }
    else { Show-Warn ("provider command '" + $cmd + "': not found (only needed for the " + $cmd + " provider)") }
}

Write-Host '---'
Write-Host ('summary: ' + $script:fails + ' failed, ' + $script:warns + ' warning(s)')
if ($script:fails -gt 0) { exit 1 }
