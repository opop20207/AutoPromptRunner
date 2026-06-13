"""Deterministic safety checks for automated agent execution.

These helpers enforce hard limits, scan prompts for destructive command patterns, and
flag risky changes by inspecting file *names* and diff statistics only. They never read
the contents of any file (in particular, never the contents of secret files), use no
network, and are deterministic. Hard-limit violations raise ``ValueError``; risky but
non-fatal situations are returned as warning strings.
"""

from __future__ import annotations

import fnmatch
import os
import re
from typing import List, Optional, Sequence

from . import config

# Artifact type names for persisted safety findings.
SAFETY_WARNING_ARTIFACT = "safety_warning"
SAFETY_BLOCKER_ARTIFACT = "safety_blocker"


# -- hard-limit validation ---------------------------------------------------


def validate_max_loops(value: Optional[int]) -> int:
    """Return ``value`` if 1 <= value <= hard limit, else raise ``ValueError``."""
    if value is None or value < 1:
        raise ValueError("max_loops must be >= 1")
    if value > config.MAX_LOOPS_HARD_LIMIT:
        raise ValueError(f"max_loops {value} exceeds the hard limit of {config.MAX_LOOPS_HARD_LIMIT}")
    return value


def validate_timeout_seconds(value: Optional[int]) -> int:
    """Return ``value`` if 1 <= value <= hard limit, else raise ``ValueError``."""
    if value is None or value < 1:
        raise ValueError("timeout_seconds must be >= 1")
    if value > config.TIMEOUT_SECONDS_HARD_LIMIT:
        raise ValueError(
            f"timeout_seconds {value} exceeds the hard limit of {config.TIMEOUT_SECONDS_HARD_LIMIT}"
        )
    return value


def _workspace_allowlist() -> List[str]:
    raw = os.environ.get(config.WORKSPACE_ALLOWLIST_ENV, "")
    return [part for part in raw.split(os.pathsep) if part.strip()]


def validate_workspace_allowed(workspace: Optional[str], allowed_roots: Optional[Sequence[str]] = None) -> Optional[str]:
    """Return ``workspace`` if it is allowed, else raise ``ValueError``.

    When an allowlist is configured (via ``allowed_roots`` or the
    ``AUTOPROMPT_WORKSPACE_ALLOWLIST`` env var), the workspace must be inside one of the
    allowed root directories. With no allowlist configured, any workspace is allowed.
    """
    if workspace is None:
        return None
    roots = list(allowed_roots) if allowed_roots is not None else _workspace_allowlist()
    if not roots:
        return workspace
    target = os.path.normcase(os.path.abspath(workspace))
    for root in roots:
        normalized_root = os.path.normcase(os.path.abspath(root))
        try:
            if os.path.commonpath([normalized_root, target]) == normalized_root:
                return workspace
        except ValueError:
            continue  # different drives on Windows
    raise ValueError(f"workspace is not within the allowed roots: {workspace}")


# -- prompt / change scanning ------------------------------------------------


def _blocked_pattern_regex(pattern: str) -> "re.Pattern[str]":
    # Match the pattern's tokens with flexible whitespace, anchored so a pattern word
    # does not match inside a larger word (e.g. "format" must not match "information").
    tokens = pattern.split()
    body = r"\s+".join(re.escape(token) for token in tokens)
    return re.compile(r"(?<!\w)" + body, re.IGNORECASE)


def scan_prompt_for_blocked_commands(prompt: Optional[str], patterns: Optional[Sequence[str]] = None) -> List[str]:
    """Return the blocked command patterns found in ``prompt`` (empty list if none)."""
    text = prompt or ""
    found: List[str] = []
    for pattern in patterns if patterns is not None else config.BLOCKED_COMMAND_PATTERNS:
        if _blocked_pattern_regex(pattern).search(text):
            found.append(pattern)
    return found


def scan_changed_files_for_secrets(
    changed_files: Optional[Sequence[str]], patterns: Optional[Sequence[str]] = None
) -> List[str]:
    """Return changed file paths whose *name* looks secret-like. Contents are not read."""
    secret_patterns = patterns if patterns is not None else config.SECRET_FILE_PATTERNS
    flagged: List[str] = []
    for path in changed_files or []:
        name = os.path.basename((path or "").strip().rstrip("/\\"))
        if not name:
            continue
        if any(fnmatch.fnmatch(name, pattern) for pattern in secret_patterns):
            flagged.append(path)
    return flagged


def _diff_stat_line_count(diff_stat: Optional[str]) -> int:
    text = diff_stat or ""
    insertions = sum(int(m) for m in re.findall(r"(\d+)\s+insertion", text))
    deletions = sum(int(m) for m in re.findall(r"(\d+)\s+deletion", text))
    return insertions + deletions


def detect_large_diff(diff_stat: Optional[str], changed_files: Optional[Sequence[str]]) -> Optional[str]:
    """Return a warning if the change is large (by file count or diff lines), else None."""
    file_count = len([f for f in (changed_files or []) if f])
    line_count = _diff_stat_line_count(diff_stat)
    if file_count > config.LARGE_CHANGED_FILES_THRESHOLD:
        return f"large change: {file_count} files changed (threshold {config.LARGE_CHANGED_FILES_THRESHOLD})"
    if line_count > config.LARGE_DIFF_LINES_THRESHOLD:
        return f"large change: {line_count} changed lines (threshold {config.LARGE_DIFF_LINES_THRESHOLD})"
    return None


def detect_risky_run(
    prompt: Optional[str],
    changed_files: Optional[Sequence[str]],
    diff_stat: Optional[str],
) -> Optional[str]:
    """Return a reason if the run is risky (secret-like changes or a large diff), else None.

    ``prompt`` is accepted for API symmetry; destructive command patterns in the prompt
    are treated as hard blockers (see ``scan_prompt_for_blocked_commands``), not merely
    risky.
    """
    reasons: List[str] = []
    secrets = scan_changed_files_for_secrets(changed_files)
    if secrets:
        reasons.append("secret-like files changed: " + ", ".join(secrets[:5]))
    large = detect_large_diff(diff_stat, changed_files)
    if large:
        reasons.append(large)
    return "; ".join(reasons) if reasons else None


def build_safety_warnings(
    changed_files: Optional[Sequence[str]] = None,
    diff_stat: Optional[str] = None,
) -> List[str]:
    """Build the list of non-fatal safety warnings for a completed step."""
    warnings: List[str] = []
    secrets = scan_changed_files_for_secrets(changed_files)
    if secrets:
        warnings.append("secret-like files changed: " + ", ".join(secrets[:10]))
    large = detect_large_diff(diff_stat, changed_files)
    if large:
        warnings.append(large)
    return warnings
