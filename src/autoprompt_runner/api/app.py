"""FastAPI application for AutoPromptRunner.

Run with::

    python -m uvicorn autoprompt_runner.api.app:app --reload

The app exposes ``/health``, ``/projects``, and ``/runs`` route groups over the same
local SQLite database the CLI uses. Handlers are thin wrappers over the existing
services; no business logic is duplicated here.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from .dependencies import require_api_auth, require_health_auth
from .routes import (
    chains,
    compare,
    events,
    export_import,
    health,
    projects,
    providers,
    recovery,
    runs,
    search,
    templates,
    worktrees,
)

app = FastAPI(
    title="AutoPromptRunner API",
    version=__version__,
    description="Local-first prompt orchestration over HTTP (optional single-token auth; no websockets).",
)

# Optional single-token auth. The dependencies are no-ops when auth is disabled (the
# default), so local development is unchanged; when enabled they require a valid
# Authorization: Bearer <token> on the protected route groups (health stays public unless
# auth.allow_unauthenticated_health is false).
_protected = [Depends(require_api_auth)]

# Permissive CORS so the local Vite dev frontend can call the API from the browser.
# This is a local-only dev backend with no auth, and credentials are not used.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, dependencies=[Depends(require_health_auth)])
app.include_router(projects.router, dependencies=_protected)
app.include_router(runs.router, dependencies=_protected)
app.include_router(templates.router, dependencies=_protected)
app.include_router(worktrees.router, dependencies=_protected)
app.include_router(search.router, dependencies=_protected)
app.include_router(compare.router, dependencies=_protected)
app.include_router(chains.router, dependencies=_protected)
app.include_router(providers.router, dependencies=_protected)
app.include_router(recovery.router, dependencies=_protected)
app.include_router(export_import.router, dependencies=_protected)
# The events router carries its own auth dependencies per-route (the SSE stream accepts a
# token via header OR query), so it is NOT wrapped with the header-only _protected dependency.
app.include_router(events.router)
