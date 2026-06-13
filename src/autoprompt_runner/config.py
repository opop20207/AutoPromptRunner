"""Safety denylists plus the numeric limits derived from :mod:`autoprompt_runner.settings`.

The numeric limits (loop / timeout bounds and large-change thresholds) are the effective
settings -- built-in defaults overlaid with a config file and ``AUTOPROMPT_*`` environment
variables -- so this module stays a single, backward-compatible import surface for the
constants the safety checks and runners already use (``config.MAX_LOOPS_HARD_LIMIT`` etc.).
The denylists (secret-file patterns, blocked commands, the workspace-allowlist env var
name) are static policy and live here. Plain data only: no behavior beyond reading
settings once at import, and a safe fallback to built-in defaults if settings fail to load.
"""

from __future__ import annotations

from . import settings as _settings


def _effective() -> "_settings.AppSettings":
    try:
        return _settings.load_settings()
    except Exception:  # never let a bad config file / env break importing the package
        return _settings.build_default_settings()


_APP = _effective()

# Loop / runtime limits (effective settings; mirror autoprompt_runner.settings).
MAX_LOOPS_DEFAULT = _APP.defaults.max_loops
MAX_LOOPS_HARD_LIMIT = _APP.safety.max_loops_hard_limit
TIMEOUT_SECONDS_DEFAULT = _APP.defaults.timeout_seconds
TIMEOUT_SECONDS_HARD_LIMIT = _APP.safety.timeout_seconds_hard_limit

# Large-change thresholds (used for warnings, not blockers).
LARGE_CHANGED_FILES_THRESHOLD = _APP.safety.large_changed_files_threshold
LARGE_DIFF_LINES_THRESHOLD = _APP.safety.large_diff_lines_threshold

# Optional workspace allowlist: a path-separator-joined list of allowed root
# directories. When set, runs may only use workspaces inside one of these roots.
WORKSPACE_ALLOWLIST_ENV = "AUTOPROMPT_WORKSPACE_ALLOWLIST"

# Secret-like file name patterns (fnmatch against the file's basename). Only names
# are inspected; file contents are never read.
SECRET_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_dsa",
    "id_ed25519",
    "secrets.*",
    "credentials.*",
    "service-account*.json",
    "*.p12",
    "*.pfx",
)

# Destructive command patterns that block a run before any agent executes.
BLOCKED_COMMAND_PATTERNS = (
    "rm -rf /",
    "rm -rf *",
    "git reset --hard",
    "git clean -fd",
    "git push --force",
    "git push -f",
    "sudo rm",
    "del /s",
    "format",
    "mkfs",
    "shutdown",
    "reboot",
)
