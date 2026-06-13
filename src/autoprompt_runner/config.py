"""Default safety settings, limits, and denylists for AutoPromptRunner.

Plain data only (no behavior, no I/O). These values drive the checks in
:mod:`autoprompt_runner.safety`. ``MAX_LOOPS_DEFAULT`` is the recommended default; the
resolution default stays 1 for backward compatibility (see ``projects.py``). The
``*_HARD_LIMIT`` values are enforced and cannot be exceeded.
"""

from __future__ import annotations

# Loop / runtime limits.
MAX_LOOPS_DEFAULT = 5
MAX_LOOPS_HARD_LIMIT = 20
TIMEOUT_SECONDS_DEFAULT = 1800
TIMEOUT_SECONDS_HARD_LIMIT = 7200

# Large-change thresholds (used for warnings, not blockers).
LARGE_CHANGED_FILES_THRESHOLD = 20
LARGE_DIFF_LINES_THRESHOLD = 1000

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
