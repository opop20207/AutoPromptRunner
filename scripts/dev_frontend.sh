#!/usr/bin/env bash
# Start the Vite frontend dev server (http://localhost:5173).
#
#   scripts/dev_frontend.sh
#
# It calls the API at http://localhost:8000 by default; override with VITE_API_BASE_URL.
# Start the backend first (scripts/dev_api.sh).
set -euo pipefail

cd "$(dirname "$0")/../frontend"
npm install
exec npm run dev
