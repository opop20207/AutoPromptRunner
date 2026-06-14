"""FastAPI application for AutoPromptRunner.

Run with::

    python -m uvicorn autoprompt_runner.api.app:app --reload

The app exposes ``/health``, ``/projects``, and ``/runs`` route groups over the same
local SQLite database the CLI uses. Handlers are thin wrappers over the existing
services; no business logic is duplicated here.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from .routes import chains, compare, health, projects, providers, recovery, runs, search, templates, worktrees

app = FastAPI(
    title="AutoPromptRunner API",
    version=__version__,
    description="Local-first prompt orchestration over HTTP (no auth, no websockets yet).",
)

# Permissive CORS so the local Vite dev frontend can call the API from the browser.
# This is a local-only dev backend with no auth, and credentials are not used.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(projects.router)
app.include_router(runs.router)
app.include_router(templates.router)
app.include_router(worktrees.router)
app.include_router(search.router)
app.include_router(compare.router)
app.include_router(chains.router)
app.include_router(providers.router)
app.include_router(recovery.router)
