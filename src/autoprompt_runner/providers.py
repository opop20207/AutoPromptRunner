"""Provider profile management: validation, availability, and runner construction.

A provider profile configures how a provider is invoked -- its ``command`` executable, a
default timeout, and optional space-separated ``default_args`` -- without hardcoding it in
the runners. A profile's ``name`` may differ from its ``type`` (for example a ``claude-fast``
profile of type ``claude-code``), so a user can keep several configurations for one runner.

This module holds the pure logic: type/command/timeout validation, an availability check
that only *discovers* the command (``shutil.which`` -- it never executes the real agent),
and construction of the correct runner for a profile. Profiles store **no secrets**; only
non-secret command/argument settings.
"""

from __future__ import annotations

import shutil
from typing import List, Optional

from . import config, storage
from .models import ProviderProfile
from .runners import AgentRunner, ClaudeCodeRunner, CodexRunner, MockRunner

# The provider runner types a profile may use.
PROVIDER_TYPES = ("mock", "claude-code", "codex")

# Built-in default profiles, seeded on demand (see ``seed_default_provider_profiles``).
DEFAULT_PROVIDER_SPECS = [
    {"name": "mock", "type": "mock", "command": "mock", "default_timeout_seconds": 30, "enabled": True},
    {"name": "claude-code", "type": "claude-code", "command": "claude", "default_timeout_seconds": 1800, "enabled": True},
    {"name": "codex", "type": "codex", "command": "codex", "default_timeout_seconds": 1800, "enabled": True},
]


class ProviderError(Exception):
    """Raised for provider-profile problems.

    ``kind`` is one of ``"invalid"`` (bad field), ``"not_found"`` (no such profile),
    ``"disabled"`` (profile is disabled), or ``"unavailable"`` (command not on PATH).
    Callers map it to a CLI exit code or an HTTP status.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def validate_provider_type(provider_type: str) -> str:
    """Return ``provider_type`` if it is a supported runner type, else raise ProviderError."""
    if provider_type not in PROVIDER_TYPES:
        raise ProviderError(
            "invalid", f"unsupported provider type '{provider_type}'. Supported: {', '.join(PROVIDER_TYPES)}"
        )
    return provider_type


def validate_provider_command(command: Optional[str]) -> str:
    """Return a cleaned command string, or raise ProviderError when empty/invalid.

    The command is the executable name or path used with ``shell=False``; it must be
    non-empty and single-token (no embedded whitespace -- arguments belong in default_args).
    """
    command = (command or "").strip()
    if not command:
        raise ProviderError("invalid", "provider command must not be empty")
    if len(command.split()) != 1:
        raise ProviderError(
            "invalid", "provider command must be a single executable (put arguments in default_args)"
        )
    return command


def validate_provider_timeout(timeout_seconds) -> int:
    """Return a valid timeout (>=1, <= the safety hard limit), or raise ProviderError."""
    try:
        value = int(timeout_seconds)
    except (TypeError, ValueError):
        raise ProviderError("invalid", "default_timeout_seconds must be an integer")
    if value < 1:
        raise ProviderError("invalid", "default_timeout_seconds must be >= 1")
    if value > config.TIMEOUT_SECONDS_HARD_LIMIT:
        raise ProviderError(
            "invalid",
            f"default_timeout_seconds must not exceed the hard limit of {config.TIMEOUT_SECONDS_HARD_LIMIT}",
        )
    return value


def check_provider_available(profile: ProviderProfile) -> bool:
    """Whether the profile can run: mock is always available; external commands must be found.

    Uses command *discovery* (``shutil.which``) only -- it never executes the real Claude
    Code or Codex CLI, so it is safe to call in tests and without those tools installed.
    """
    if profile.type == "mock":
        return True
    return shutil.which(profile.command) is not None


def ensure_provider_runnable(profile: ProviderProfile) -> None:
    """Raise ProviderError if the profile is disabled or its command is unavailable."""
    if not profile.enabled:
        raise ProviderError("disabled", f"provider '{profile.name}' is disabled")
    if not check_provider_available(profile):
        raise ProviderError(
            "unavailable",
            f"provider '{profile.name}' is not available: command '{profile.command}' not found on PATH",
        )


def parse_default_args(default_args: Optional[str]) -> List[str]:
    """Split stored ``default_args`` into an argv list on whitespace (no shell parsing)."""
    if not default_args:
        return []
    return default_args.split()


def build_runner_for_profile(
    profile: ProviderProfile, workspace: Optional[str], timeout_seconds: Optional[int]
) -> AgentRunner:
    """Construct the runner for ``profile.type`` using the profile's command and args.

    The timeout is the explicit ``timeout_seconds`` when given, else the profile default.
    """
    effective_timeout = timeout_seconds if timeout_seconds is not None else profile.default_timeout_seconds
    extra_args = parse_default_args(profile.default_args)
    if profile.type == "mock":
        return MockRunner()
    if profile.type == "claude-code":
        return ClaudeCodeRunner(
            command=profile.command, timeout_seconds=effective_timeout,
            workspace=workspace, extra_args=extra_args,
        )
    if profile.type == "codex":
        return CodexRunner(
            command=profile.command, timeout_seconds=effective_timeout,
            workspace=workspace, extra_args=extra_args,
        )
    raise ProviderError("invalid", f"unsupported provider type '{profile.type}'")


def seed_default_provider_profiles(db_path: str, force: bool = False) -> dict:
    """Seed the built-in default profiles (mock / claude-code / codex) if missing."""
    return storage.seed_default_provider_profiles(db_path, DEFAULT_PROVIDER_SPECS, force=force)


def availability_summary(db_path: str) -> List[dict]:
    """Return a compact availability summary for every profile (command discovery only)."""
    return [
        {
            "name": profile.name,
            "type": profile.type,
            "command": profile.command,
            "enabled": profile.enabled,
            "available": check_provider_available(profile),
        }
        for profile in storage.list_provider_profiles(db_path)
    ]
