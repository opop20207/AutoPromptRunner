"""FastAPI application for AutoPromptRunner.

Run with::

    python -m uvicorn autoprompt_runner.api.app:app --reload

The app exposes ``/health``, ``/projects``, and ``/runs`` route groups over the same
local SQLite database the CLI uses. Handlers are thin wrappers over the existing
services; no business logic is duplicated here.
"""

from __future__ import annotations

from fastapi import FastAPI

from .. import __version__
from .routes import health, projects, runs

app = FastAPI(
    title="AutoPromptRunner API",
    version=__version__,
    description="Local-first prompt orchestration over HTTP (no auth, no websockets, no frontend yet).",
)

app.include_router(health.router)
app.include_router(projects.router)
app.include_router(runs.router)
