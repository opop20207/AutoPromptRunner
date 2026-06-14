# AutoPromptRunner frontend

A minimal **React + Vite + TypeScript** single-page UI over the AutoPromptRunner HTTP API.
No router, state library, or UI framework -- plain components and CSS.

## Develop

```
npm install
npm run dev      # http://localhost:5173
```

Start the backend first (from the repo root: `pip install -e ".[api]"` then
`scripts/dev_api.sh`, or `python -m uvicorn autoprompt_runner.api.app:app --reload`). The UI
calls the API at `http://localhost:8000` by default; override with the `VITE_API_BASE_URL`
environment variable.

## Build / type-check

```
npm run build    # tsc (type-check) + vite build -> dist/
npm run preview  # serve the production build locally
```

From the repo root you can also use the helper scripts:
`scripts/install_frontend.sh` (npm install), `scripts/dev_frontend.sh` (dev server),
and `scripts/build_frontend.sh` (production build).

## Configuration

The UI calls the backend at `http://localhost:8000` by default. Override it with the
`VITE_API_BASE_URL` environment variable at dev/build time, for example:

```
VITE_API_BASE_URL=http://127.0.0.1:9000 npm run dev
```

## Known limitations

- No authentication — intended for local, single-user use only.
- Run logs use polling (no WebSocket / SSE live streaming).
- No router or state library; navigation is simple local state.
- Requires the backend API to be running (`scripts/dev_api.sh`).

## What it covers

Health (with config metadata), projects, prompt templates, runs (create / queue / approve /
reject / cancel), run detail with steps, changed files, diff stat, artifact viewer, live
log polling, safety panel, workspace locks, the run queue, and Git worktrees. See the repo
[README](../README.md) for the full feature tour and local setup flow.
