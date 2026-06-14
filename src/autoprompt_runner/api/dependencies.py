"""FastAPI dependencies.

``get_db_path`` returns the resolved SQLite database path used by every request, taken
from the same settings loader as the CLI/worker (config file + ``AUTOPROMPT_*`` env). Tests
override it (via ``app.dependency_overrides``) to point at a temporary database.

``require_api_auth`` / ``require_health_auth`` enforce the optional single-token API auth
(see ``autoprompt_runner.auth``). They are no-ops when auth is disabled (the default), so
existing local behavior is unchanged. A missing or invalid token yields a clean ``401``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from .. import auth, settings, storage

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def get_db_path() -> str:
    """Return the resolved default SQLite path from settings, ensuring the database exists."""
    return storage.init_db(settings.load_settings().storage.db_path)


def require_api_auth(authorization: Optional[str] = Header(default=None)) -> None:
    """Protected-route dependency: require a valid bearer token when auth is enabled."""
    try:
        auth.require_api_auth(authorization, settings.load_settings())
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers=_UNAUTHORIZED_HEADERS)


def require_health_auth(authorization: Optional[str] = Header(default=None)) -> None:
    """Health dependency: public unless auth is enabled AND unauthenticated health is off."""
    app_settings = settings.load_settings()
    if not auth.is_auth_enabled(app_settings) or app_settings.auth.allow_unauthenticated_health:
        return
    try:
        auth.require_api_auth(authorization, app_settings)
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers=_UNAUTHORIZED_HEADERS)
