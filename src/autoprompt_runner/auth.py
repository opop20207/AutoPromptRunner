"""Optional single-token API authentication (local-first access protection).

AutoPromptRunner can run real coding agents against local repositories, so even though it
binds to ``127.0.0.1`` by default, the HTTP API can optionally require a single bearer
token. This module holds the pure, framework-free logic (token generation, header parsing,
constant-time comparison, redaction); the FastAPI wiring lives in
``autoprompt_runner.api.dependencies``. It uses only the standard library (``secrets`` /
``hmac``), never prints or logs the token, and is importable without the optional API extra.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Optional

# Number of random bytes for a generated token (secrets.token_urlsafe length parameter).
_TOKEN_BYTES = 32


class AuthError(Exception):
    """Raised when authentication is required but the bearer token is missing or invalid."""


def is_auth_enabled(settings) -> bool:
    """Whether API auth is turned on in the effective settings."""
    return bool(getattr(settings, "auth", None) and settings.auth.enabled)


def generate_api_token() -> str:
    """Return a new cryptographically secure URL-safe API token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def redact_token(value: Optional[str]) -> str:
    """Return a safe, value-free description of a token (never the token itself)."""
    return "(set, redacted)" if (value or "").strip() else "(unset)"


def _extract_bearer(auth_header: Optional[str]) -> Optional[str]:
    """Return the token from an ``Authorization: Bearer <token>`` header, or ``None``."""
    if not auth_header:
        return None
    parts = auth_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def validate_bearer_token(auth_header: Optional[str], settings) -> bool:
    """Return True if the bearer token in ``auth_header`` matches the configured token.

    Uses ``hmac.compare_digest`` for a constant-time comparison. Returns False when either
    the supplied or the configured token is empty.
    """
    supplied = _extract_bearer(auth_header)
    configured = (getattr(settings.auth, "api_token", "") or "") if getattr(settings, "auth", None) else ""
    if not supplied or not configured:
        return False
    return hmac.compare_digest(supplied, configured)


def require_api_auth(auth_header: Optional[str], settings) -> None:
    """Raise :class:`AuthError` if auth is enabled and the bearer token is missing/invalid.

    A no-op when auth is disabled (the default), preserving existing local behavior.
    """
    if not is_auth_enabled(settings):
        return
    if not validate_bearer_token(auth_header, settings):
        raise AuthError("missing or invalid API token")
