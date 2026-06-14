#!/usr/bin/env bash
# Prepare a local v0.1 release artifact set. It runs the full check suite, builds the
# frontend, builds the Python package (if the 'build' module is available), and assembles a
# release folder under dist/release-v0.1. It NEVER publishes to PyPI, creates a GitHub
# release, or pushes tags -- everything stays local. No external AI tool is invoked.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(python -c 'import sys; sys.path.insert(0, "src"); import autoprompt_runner; print(autoprompt_runner.__version__)' 2>/dev/null || echo "0.1")"
REL="$ROOT/dist/release-v0.1"

echo "== full check suite =="
bash "$ROOT/scripts/check_all.sh"

echo "== frontend build =="
bash "$ROOT/scripts/build_frontend.sh"

echo "== python package build =="
if python -c "import build" >/dev/null 2>&1; then
  python -m build
else
  echo "  the Python 'build' module is not installed."
  echo "  install it and re-run to produce wheel/sdist: python -m pip install build"
fi

echo "== assembling $REL =="
mkdir -p "$REL"
# Copy built Python distributions, if any were produced.
shopt -s nullglob
dists=("$ROOT"/dist/*.whl "$ROOT"/dist/*.tar.gz)
if [ "${#dists[@]}" -gt 0 ]; then
  cp "${dists[@]}" "$REL"/
fi
shopt -u nullglob
# Copy the built frontend bundle and the README for convenience.
if [ -d "$ROOT/frontend/dist" ]; then
  rm -rf "$REL/frontend-dist"
  cp -r "$ROOT/frontend/dist" "$REL/frontend-dist"
fi
cp "$ROOT/README.md" "$REL"/ 2>/dev/null || true

echo "---"
echo "Local release v$VERSION assembled under: $REL"
echo "(Not published. To publish manually: python -m pip install build twine, then twine upload dist/*)"
