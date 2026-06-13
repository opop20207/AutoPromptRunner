"""Workspace execution locks.

AutoPromptRunner may drive Claude Code or Codex against real repositories. Two active
runs in the same workspace can corrupt edits, mix diffs, or produce invalid run history.
This module enforces **one active lock per workspace**: a run acquires the lock only for
the duration of actual runner execution and releases it as soon as the run reaches a
terminal state *or* pauses at ``WAITING_APPROVAL`` (so human review never blocks the
workspace forever). A lock also carries an ``expires_at`` so a crashed process cannot hold
a workspace forever -- stale locks are reclaimed on the next acquire/list.

The persistence lives in :mod:`autoprompt_runner.storage`; this module owns path
normalization, the TTL policy, and the acquire/release logic (and re-exports the lock
status constants).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import storage
from .models import RunLock
from .storage import LOCK_ACTIVE, LOCK_EXPIRED, LOCK_RELEASED  # noqa: F401  (re-exported)

LOCK_STATUSES = (LOCK_ACTIVE, LOCK_RELEASED, LOCK_EXPIRED)

# Artifact type recorded when a run is blocked from executing by a workspace lock.
LOCK_BLOCKER_ARTIFACT = "lock_blocker"

# Extra seconds added on top of a run's timeout before its lock may expire.
_TTL_GRACE_SECONDS = 300
_FALLBACK_TIMEOUT_SECONDS = 1800


class LockConflictError(Exception):
    """Raised by :func:`acquire_lock` when another active run already holds the workspace."""

    def __init__(self, workspace_path: str, holder_run_id: int) -> None:
        super().__init__(f"workspace already locked by run {holder_run_id}: {workspace_path}")
        self.workspace_path = workspace_path
        self.holder_run_id = holder_run_id


def normalize_workspace(path: Optional[str]) -> str:
    """Normalize a workspace path for lock comparison.

    Resolves to an absolute path and applies OS-aware case/separator normalization, so
    differently-written paths to the same directory map to one lock key. Does not touch
    the filesystem (no symlink resolution).
    """
    if not path:
        return ""
    return os.path.normcase(os.path.abspath(path))


def default_lock_ttl_seconds(timeout_seconds: Optional[int]) -> int:
    """Return the lock TTL: the run's timeout plus a fixed grace period."""
    base = int(timeout_seconds) if timeout_seconds else _FALLBACK_TIMEOUT_SECONDS
    return base + _TTL_GRACE_SECONDS


def current_owner() -> str:
    """Return a compact identifier for the current process (the lock owner)."""
    return f"pid-{os.getpid()}"


def active_lock_for_workspace(db_path: str, workspace: Optional[str]) -> Optional[RunLock]:
    """Return the current ACTIVE lock for ``workspace`` (normalized) or ``None``."""
    if not workspace:
        return None
    return storage.get_active_lock_for_workspace(db_path, normalize_workspace(workspace))


def expire_locks(db_path: str) -> int:
    """Expire any ACTIVE locks past their ``expires_at`` (using the current time)."""
    return storage.expire_old_locks(db_path, datetime.now(timezone.utc).isoformat())


def acquire_lock(
    db_path: str,
    workspace: Optional[str],
    run_id: int,
    owner: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> Optional[RunLock]:
    """Acquire the workspace lock for ``run_id``.

    Returns ``None`` when there is no workspace (no lock needed). If the same run already
    holds the lock it is returned unchanged. Raises :class:`LockConflictError` when another
    active run holds the workspace (stale locks are expired first).
    """
    if not workspace:
        return None
    norm = normalize_workspace(workspace)
    now = datetime.now(timezone.utc)
    storage.expire_old_locks(db_path, now.isoformat())
    existing = storage.get_active_lock_for_workspace(db_path, norm)
    if existing is not None and existing.run_id != run_id:
        raise LockConflictError(norm, existing.run_id)
    if existing is not None:
        return existing  # this run already holds the lock
    expires_at = (now + timedelta(seconds=default_lock_ttl_seconds(timeout_seconds))).isoformat()
    storage.create_run_lock(
        db_path,
        workspace_path=norm,
        run_id=run_id,
        status=LOCK_ACTIVE,
        owner=owner or current_owner(),
        expires_at=expires_at,
    )
    return storage.get_active_lock_for_workspace(db_path, norm)


def release_lock(db_path: str, run_id: int) -> int:
    """Release this run's ACTIVE lock(s). Returns how many were released."""
    return storage.release_run_lock(db_path, run_id)
