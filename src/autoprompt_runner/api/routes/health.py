"""Health route.

Returns liveness plus compact, non-secret config metadata so a client can confirm the
backend's effective settings (db path, default provider, queue poll interval, and the
safety hard limits). No environment dumps or secrets are included.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ... import settings as settings_mod

router = APIRouter(tags=["health"])


class HealthConfig(BaseModel):
    db_path: str
    default_provider: str
    queue_poll_interval_seconds: float
    max_loops_hard_limit: int
    timeout_seconds_hard_limit: int
    # Whether API auth is enabled (so a client knows to send a token). The token itself is
    # never included in this (or any) response.
    auth_enabled: bool = False


class HealthResponse(BaseModel):
    status: str
    service: str
    config: HealthConfig


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = settings_mod.load_settings()
    return HealthResponse(
        status="ok",
        service="AutoPromptRunner",
        config=HealthConfig(
            db_path=settings.storage.db_path,
            default_provider=settings.defaults.provider,
            queue_poll_interval_seconds=settings.queue.poll_interval_seconds,
            max_loops_hard_limit=settings.safety.max_loops_hard_limit,
            timeout_seconds_hard_limit=settings.safety.timeout_seconds_hard_limit,
            auth_enabled=settings.auth.enabled,
        ),
    )
