"""Centralized application settings for AutoPromptRunner.

Settings are resolved in layers, lowest precedence first:

1. built-in defaults (the dataclass defaults below),
2. a local TOML config file (``--config`` / ``AUTOPROMPT_CONFIG`` / ``./autoprompt.toml``
   / ``./.autoprompt/config.toml``),
3. ``AUTOPROMPT_*`` environment variables.

Explicit CLI flags and project profiles still win at command-execution time (they are
applied by the CLI / run service on top of these settings). This module is the single
source of truth for the configurable values -- :mod:`autoprompt_runner.config` derives its
constants from here. It uses only the Python standard library (``tomllib`` on 3.11+, with
``tomli`` as a fallback only if the interpreter lacks ``tomllib``). It reads no secrets and
prints nothing.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on <3.11
    import tomli as tomllib  # type: ignore

_DEFAULT_DB_PATH = os.path.join(".autoprompt", "autoprompt.db")
_DEFAULT_WORKTREES_DIR = os.path.join(".autoprompt", "worktrees")

# Environment variable that points at a config file (resolved before reading sections).
CONFIG_ENV = "AUTOPROMPT_CONFIG"


class SettingsError(ValueError):
    """Raised for an invalid config value or a malformed/missing config file."""


@dataclass
class StorageSettings:
    db_path: str = _DEFAULT_DB_PATH


@dataclass
class DefaultRunSettings:
    provider: str = "mock"
    workspace: str = ""
    max_loops: int = 5
    require_approval: bool = True
    timeout_seconds: int = 1800


@dataclass
class SafetySettings:
    max_loops_hard_limit: int = 20
    timeout_seconds_hard_limit: int = 7200
    large_changed_files_threshold: int = 20
    large_diff_lines_threshold: int = 1000


@dataclass
class QueueSettings:
    poll_interval_seconds: float = 2.0


@dataclass
class ApiSettings:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class WorktreeSettings:
    base_dir: str = _DEFAULT_WORKTREES_DIR


@dataclass
class AppSettings:
    storage: StorageSettings = field(default_factory=StorageSettings)
    defaults: DefaultRunSettings = field(default_factory=DefaultRunSettings)
    safety: SafetySettings = field(default_factory=SafetySettings)
    queue: QueueSettings = field(default_factory=QueueSettings)
    api: ApiSettings = field(default_factory=ApiSettings)
    worktrees: WorktreeSettings = field(default_factory=WorktreeSettings)


def build_default_settings() -> AppSettings:
    """Return a fresh :class:`AppSettings` with only the built-in defaults."""
    return AppSettings()


# -- config file ------------------------------------------------------------


def _resolve_config_path(config_path: Optional[str]) -> Optional[str]:
    """Resolve the config file to read, following the documented search order."""
    if config_path:
        if not os.path.isfile(config_path):
            raise SettingsError(f"config file not found: {config_path}")
        return config_path
    env_path = os.environ.get(CONFIG_ENV)
    if env_path:
        if not os.path.isfile(env_path):
            raise SettingsError(f"{CONFIG_ENV} file not found: {env_path}")
        return env_path
    for candidate in ("autoprompt.toml", os.path.join(".autoprompt", "config.toml")):
        if os.path.isfile(candidate):
            return candidate
    return None


def _read_toml(path: str) -> Dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SettingsError(f"could not read config file {path}: {exc}") from exc


def _apply_toml(settings: AppSettings, data: Dict[str, Any]) -> None:
    storage = data.get("storage", {})
    if "db_path" in storage:
        settings.storage.db_path = str(storage["db_path"])

    defaults = data.get("defaults", {})
    if "provider" in defaults:
        settings.defaults.provider = str(defaults["provider"])
    if "workspace" in defaults:
        settings.defaults.workspace = str(defaults["workspace"])
    if "max_loops" in defaults:
        settings.defaults.max_loops = int(defaults["max_loops"])
    if "require_approval" in defaults:
        settings.defaults.require_approval = bool(defaults["require_approval"])
    if "timeout_seconds" in defaults:
        settings.defaults.timeout_seconds = int(defaults["timeout_seconds"])

    safety = data.get("safety", {})
    if "max_loops_hard_limit" in safety:
        settings.safety.max_loops_hard_limit = int(safety["max_loops_hard_limit"])
    if "timeout_seconds_hard_limit" in safety:
        settings.safety.timeout_seconds_hard_limit = int(safety["timeout_seconds_hard_limit"])
    if "large_changed_files_threshold" in safety:
        settings.safety.large_changed_files_threshold = int(safety["large_changed_files_threshold"])
    if "large_diff_lines_threshold" in safety:
        settings.safety.large_diff_lines_threshold = int(safety["large_diff_lines_threshold"])

    queue = data.get("queue", {})
    if "poll_interval_seconds" in queue:
        settings.queue.poll_interval_seconds = float(queue["poll_interval_seconds"])

    api = data.get("api", {})
    if "host" in api:
        settings.api.host = str(api["host"])
    if "port" in api:
        settings.api.port = int(api["port"])

    worktrees = data.get("worktrees", {})
    if "base_dir" in worktrees:
        settings.worktrees.base_dir = str(worktrees["base_dir"])


# -- environment overrides --------------------------------------------------


def _env_str(name: str, current: str) -> str:
    raw = os.environ.get(name)
    return raw if raw is not None else current


def _env_int(name: str, current: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return current
    try:
        return int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, current: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return current
    try:
        return float(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be a number, got {raw!r}") from exc


def _apply_env(settings: AppSettings) -> None:
    settings.storage.db_path = _env_str("AUTOPROMPT_DB_PATH", settings.storage.db_path)
    settings.defaults.provider = _env_str("AUTOPROMPT_DEFAULT_PROVIDER", settings.defaults.provider)
    settings.defaults.workspace = _env_str("AUTOPROMPT_DEFAULT_WORKSPACE", settings.defaults.workspace)
    settings.defaults.max_loops = _env_int("AUTOPROMPT_MAX_LOOPS_DEFAULT", settings.defaults.max_loops)
    settings.defaults.timeout_seconds = _env_int(
        "AUTOPROMPT_TIMEOUT_SECONDS_DEFAULT", settings.defaults.timeout_seconds
    )
    settings.safety.max_loops_hard_limit = _env_int(
        "AUTOPROMPT_MAX_LOOPS_HARD_LIMIT", settings.safety.max_loops_hard_limit
    )
    settings.safety.timeout_seconds_hard_limit = _env_int(
        "AUTOPROMPT_TIMEOUT_SECONDS_HARD_LIMIT", settings.safety.timeout_seconds_hard_limit
    )
    settings.queue.poll_interval_seconds = _env_float(
        "AUTOPROMPT_QUEUE_POLL_INTERVAL_SECONDS", settings.queue.poll_interval_seconds
    )
    settings.api.host = _env_str("AUTOPROMPT_API_HOST", settings.api.host)
    settings.api.port = _env_int("AUTOPROMPT_API_PORT", settings.api.port)
    settings.worktrees.base_dir = _env_str("AUTOPROMPT_WORKTREE_BASE_DIR", settings.worktrees.base_dir)


# -- public API -------------------------------------------------------------


def load_settings(config_path: Optional[str] = None) -> AppSettings:
    """Return the effective settings: defaults <- config file <- environment.

    Explicit CLI flags / project profiles are layered on top by callers at execution time.
    """
    settings = build_default_settings()
    path = _resolve_config_path(config_path)
    if path is not None:
        _apply_toml(settings, _read_toml(path))
    _apply_env(settings)
    return settings


def validate_settings(settings: AppSettings) -> None:
    """Raise :class:`SettingsError` if any value is invalid."""
    if not (settings.storage.db_path or "").strip():
        raise SettingsError("storage.db_path must not be empty")
    if settings.defaults.max_loops < 1:
        raise SettingsError("defaults.max_loops must be >= 1")
    if settings.safety.max_loops_hard_limit < 1:
        raise SettingsError("safety.max_loops_hard_limit must be >= 1")
    if settings.defaults.max_loops > settings.safety.max_loops_hard_limit:
        raise SettingsError("defaults.max_loops must not exceed safety.max_loops_hard_limit")
    if settings.defaults.timeout_seconds < 1:
        raise SettingsError("defaults.timeout_seconds must be >= 1")
    if settings.safety.timeout_seconds_hard_limit < 1:
        raise SettingsError("safety.timeout_seconds_hard_limit must be >= 1")
    if settings.defaults.timeout_seconds > settings.safety.timeout_seconds_hard_limit:
        raise SettingsError("defaults.timeout_seconds must not exceed safety.timeout_seconds_hard_limit")
    if settings.safety.large_changed_files_threshold < 1:
        raise SettingsError("safety.large_changed_files_threshold must be >= 1")
    if settings.safety.large_diff_lines_threshold < 1:
        raise SettingsError("safety.large_diff_lines_threshold must be >= 1")
    if settings.queue.poll_interval_seconds <= 0:
        raise SettingsError("queue.poll_interval_seconds must be > 0")
    if not (1 <= settings.api.port <= 65535):
        raise SettingsError("api.port must be between 1 and 65535")


def settings_to_dict(settings: AppSettings) -> Dict[str, Any]:
    """Return the settings as a nested dict (mirrors the TOML sections; no secrets)."""
    return asdict(settings)
