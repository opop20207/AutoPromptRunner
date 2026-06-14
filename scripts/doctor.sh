#!/usr/bin/env bash
# Environment diagnostics for AutoPromptRunner. Prints a compact report and exits non-zero
# only when a REQUIRED check fails (Python, package import, SQLite). Optional checks
# (Node/npm, config validity, frontend deps, and the claude / codex provider commands) only
# warn -- a missing external agent never fails this script. No external AI tool is invoked.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# Make the package importable whether or not it is pip-installed (mirrors check_all.sh).
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

fails=0
warns=0
pass() { printf '  [ok]   %s\n' "$1"; }
warn() { printf '  [warn] %s\n' "$1"; warns=$((warns + 1)); }
fail() { printf '  [FAIL] %s\n' "$1"; fails=$((fails + 1)); }

echo "AutoPromptRunner doctor"

# -- required ---------------------------------------------------------------
if py_ver="$(python --version 2>&1)"; then
  pass "python: $py_ver"
else
  fail "python: not found on PATH"
fi

if sqlite_ver="$(python -c 'import sqlite3; print(sqlite3.sqlite_version)' 2>/dev/null)"; then
  pass "sqlite (via python): $sqlite_ver"
else
  fail "sqlite: Python sqlite3 module unavailable"
fi

if cli_ver="$(python -m autoprompt_runner.cli version 2>/dev/null)"; then
  pass "autoprompt-runner CLI: $cli_ver"
else
  fail "autoprompt-runner CLI: import/run failed (is the package installed or src on PYTHONPATH?)"
fi

# -- optional ---------------------------------------------------------------
if node_ver="$(node --version 2>/dev/null)"; then
  pass "node: $node_ver"
else
  warn "node: not found (needed only to build/run the web UI)"
fi
if npm_ver="$(npm --version 2>/dev/null)"; then
  pass "npm: $npm_ver"
else
  warn "npm: not found (needed only to build/run the web UI)"
fi

if [ -d frontend/node_modules ]; then
  pass "frontend dependencies: installed"
else
  warn "frontend dependencies: missing (run scripts/install_frontend.sh)"
fi

if python -m autoprompt_runner.cli config validate >/dev/null 2>&1; then
  pass "config: valid"
else
  warn "config: not initialized or invalid (run: autoprompt-runner config init / config validate)"
fi

# Optional provider commands -- never fail the doctor on these.
for cmd in claude codex; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "provider command '$cmd': found"
  else
    warn "provider command '$cmd': not found (only needed for the $cmd provider)"
  fi
done

echo "---"
echo "summary: $fails failed, $warns warning(s)"
if [ "$fails" -gt 0 ]; then
  exit 1
fi
