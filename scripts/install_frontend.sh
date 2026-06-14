#!/usr/bin/env bash
# Install the frontend's npm dependencies (locally, inside frontend/node_modules).
# No global packages are installed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"

echo "== npm install (frontend) =="
npm install
