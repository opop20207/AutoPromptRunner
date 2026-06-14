#!/usr/bin/env bash
# Build the frontend (TypeScript type-check + Vite production build -> frontend/dist).
# Fails cleanly (non-zero) on any TypeScript or build error.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"

if [ ! -d node_modules ]; then
  echo "== npm install (frontend deps missing) =="
  npm install
fi

echo "== frontend build (tsc + vite) =="
npm run build
