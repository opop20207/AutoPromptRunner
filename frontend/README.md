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
```

## What it covers

Health (with config metadata), projects, prompt templates, runs (create / queue / approve /
reject / cancel), run detail with steps, changed files, diff stat, artifact viewer, live
log polling, safety panel, workspace locks, the run queue, and Git worktrees. See the repo
[README](../README.md) for the full feature tour and local setup flow.
