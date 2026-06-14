"""Run checkpoints and safe, explicit rollback.

Before an agent executes against a real workspace, AutoPromptRunner records a **checkpoint**:
the read-only Git state of the workspace (HEAD commit, branch, and porcelain status). This is
metadata only -- it stores no file contents and creates **no commit, tag, or stash** by
default; the captured HEAD itself is the ref a rollback would restore. Checkpointing is
best-effort and never fails a run: a missing or non-Git workspace is recorded as ``SKIPPED``.

Rollback is **always explicit and never automatic**. There are two supported modes:

1. *metadata-only check* -- :func:`build_rollback_plan` shows what would be restored and
   changes nothing on disk.
2. *safe reset* -- :func:`rollback_checkpoint` runs ``git reset --hard <git_head_before>``,
   but only with ``confirm=True``, only after recording a safety-warning artifact, and it
   refuses when the workspace had uncommitted changes that were not created by the run (or is
   held by an active run lock) unless ``force=True``.

A *revert-patch* mode (reverting only the run's changes) is intentionally **not implemented**;
see the README "future work". This module uses Git only (no filesystem snapshotting), never
runs ``git clean`` / push / pull / merge / rebase, never deletes files, and never reads or
prints secret file contents.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from . import events, git_utils, locks, safety, storage
from .models import RunCheckpoint

# Checkpoint statuses (mirror storage constants for callers that import this module).
CHECKPOINT_CREATED = storage.CHECKPOINT_CREATED
CHECKPOINT_RESTORED = storage.CHECKPOINT_RESTORED
CHECKPOINT_FAILED = storage.CHECKPOINT_FAILED
CHECKPOINT_SKIPPED = storage.CHECKPOINT_SKIPPED

# The only supported rollback mode (revert-patch mode is future work; see the README).
ROLLBACK_MODE_RESET = "reset"

# Artifact type recorded for a rollback action (the pre-reset warning reuses the safety type).
ARTIFACT_CHECKPOINT_ROLLBACK = "checkpoint_rollback"

# Event types (emitted best-effort; the run-event ``type`` column is a free-text string).
EVENT_CHECKPOINT_CREATED = "checkpoint_created"
EVENT_CHECKPOINT_ROLLED_BACK = "checkpoint_rolled_back"

# Skip reasons stored in ``restore_error`` for a SKIPPED checkpoint.
_SKIP_NO_WORKSPACE = "workspace not set; checkpoint skipped"
_SKIP_MISSING_WORKSPACE = "workspace path does not exist; checkpoint skipped"
_SKIP_NOT_GIT = "workspace is not a git repository; checkpoint skipped"


class CheckpointError(Exception):
    """Raised for checkpoint/rollback problems.

    ``kind`` is ``"not_found"`` (checkpoint missing), ``"not_confirmed"`` (rollback attempted
    without explicit confirmation), or ``"unsafe"`` (rollback refused: nothing to restore, the
    workspace is no longer a Git repo, or it has changes not created by the run / is locked and
    ``force`` was not given). Callers map it to a CLI exit code or an HTTP status (404/400/409).
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class RollbackPlan:
    """A read-only description of what a rollback would do (no files are changed)."""

    checkpoint_id: int
    run_id: int
    workspace_path: str
    status: str
    mode: str
    target_head: Optional[str]
    target_branch: Optional[str]
    current_head: Optional[str]
    current_branch: Optional[str]
    is_git_repo: bool
    preexisting_dirty: bool
    current_dirty: bool
    workspace_locked: bool
    can_rollback: bool
    requires_force: bool
    safe: bool
    summary: str
    warnings: List[str] = field(default_factory=list)


@dataclass
class RollbackResult:
    """The outcome of an executed rollback."""

    checkpoint_id: int
    run_id: int
    status: str
    restored: bool
    target_head: Optional[str]
    git_head_after: Optional[str]
    message: str
    error: Optional[str] = None


# -- detection (read-only) ---------------------------------------------------


def detect_preexisting_dirty_state(checkpoint: RunCheckpoint) -> bool:
    """True if the workspace already had uncommitted changes *before* the run.

    Derived from the checkpoint's captured ``git_status_before`` (no disk read). Such changes
    were not created by the run, so a ``git reset --hard`` would discard them too -- which is
    why their presence force-gates a rollback.
    """
    return bool((checkpoint.git_status_before or "").strip())


def detect_post_run_dirty_state(checkpoint: RunCheckpoint) -> bool:
    """True if the checkpoint's workspace has uncommitted changes *now* (live read-only check)."""
    workspace = checkpoint.workspace_path
    if not workspace or not git_utils.is_git_repository(workspace):
        return False
    return git_utils.is_git_dirty(workspace)


# -- create ------------------------------------------------------------------


def create_checkpoint(
    db_path: str, run_id: int, step_id: Optional[int], workspace: Optional[str]
) -> RunCheckpoint:
    """Capture a pre-execution checkpoint for ``run_id`` and return the stored record.

    Records the read-only Git HEAD / branch / porcelain status of ``workspace``. A missing or
    non-Git workspace is recorded as ``SKIPPED`` (with the reason in ``restore_error``) and
    never raises -- so checkpointing cannot fail a run. No commit, tag, or stash is created.
    """
    db_path = storage.init_db(db_path)
    skip_reason = _skip_reason(workspace)
    if skip_reason is not None:
        cid = storage.create_checkpoint_record(
            db_path, run_id=run_id, workspace_path=workspace or "", step_id=step_id,
            status=CHECKPOINT_SKIPPED, restore_error=skip_reason,
        )
        return storage.get_checkpoint_by_id(db_path, cid)

    try:
        head = git_utils.get_git_head(workspace)
        branch = git_utils.get_git_branch(workspace)
        status_porcelain = git_utils.get_git_status_porcelain(workspace)
    except Exception as exc:  # noqa: BLE001 - capture must never break a run
        cid = storage.create_checkpoint_record(
            db_path, run_id=run_id, workspace_path=workspace, step_id=step_id,
            status=CHECKPOINT_SKIPPED, restore_error=f"git capture failed; checkpoint skipped: {exc}",
        )
        return storage.get_checkpoint_by_id(db_path, cid)

    cid = storage.create_checkpoint_record(
        db_path, run_id=run_id, workspace_path=workspace, step_id=step_id,
        git_head_before=head, git_branch_before=branch, git_status_before=status_porcelain,
        checkpoint_ref=head, status=CHECKPOINT_CREATED,
    )
    dirty = bool((status_porcelain or "").strip())
    _emit(
        db_path, run_id, EVENT_CHECKPOINT_CREATED,
        message=f"checkpoint #{cid} captured (HEAD {head or 'none'}{', dirty' if dirty else ''})",
        payload={"checkpoint_id": cid, "head": head, "branch": branch, "dirty": dirty},
    )
    return storage.get_checkpoint_by_id(db_path, cid)


def _skip_reason(workspace: Optional[str]) -> Optional[str]:
    """Return why a checkpoint should be skipped for ``workspace``, or None to proceed."""
    if not workspace:
        return _SKIP_NO_WORKSPACE
    if not os.path.isdir(workspace):
        return _SKIP_MISSING_WORKSPACE
    if not git_utils.is_git_repository(workspace):
        return _SKIP_NOT_GIT
    return None


# -- query -------------------------------------------------------------------


def list_checkpoints(db_path: str, run_id: int) -> List[RunCheckpoint]:
    """Return checkpoints for ``run_id``, newest first."""
    return list(reversed(storage.list_checkpoints_for_run(db_path, run_id)))


def get_checkpoint(db_path: str, checkpoint_id: int) -> Optional[RunCheckpoint]:
    """Return the checkpoint with ``checkpoint_id`` or ``None``."""
    return storage.get_checkpoint_by_id(db_path, checkpoint_id)


def get_latest_checkpoint(db_path: str, run_id: int) -> Optional[RunCheckpoint]:
    """Return the most recent checkpoint for ``run_id`` (or None)."""
    return storage.get_latest_checkpoint_for_run(db_path, run_id)


def _require_checkpoint(db_path: str, checkpoint_id: int) -> RunCheckpoint:
    checkpoint = storage.get_checkpoint_by_id(db_path, checkpoint_id)
    if checkpoint is None:
        raise CheckpointError("not_found", f"checkpoint {checkpoint_id} not found")
    return checkpoint


# -- rollback ----------------------------------------------------------------


def build_rollback_plan(db_path: str, checkpoint_id: int) -> RollbackPlan:
    """Return a read-only plan describing what rolling back ``checkpoint_id`` would do.

    Changes nothing on disk (the "metadata-only rollback check" mode). The plan reports the
    target HEAD/branch, the current HEAD/branch, whether the workspace was dirty before the run
    or is dirty now, whether it is held by an active lock, and whether a rollback would require
    ``force``.
    """
    db_path = storage.init_db(db_path)
    checkpoint = _require_checkpoint(db_path, checkpoint_id)
    workspace = checkpoint.workspace_path
    target_head = checkpoint.git_head_before

    is_git = bool(workspace) and git_utils.is_git_repository(workspace)
    current_head = git_utils.get_git_head(workspace) if is_git else None
    current_branch = git_utils.get_git_branch(workspace) if is_git else None
    preexisting_dirty = detect_preexisting_dirty_state(checkpoint)
    current_dirty = git_utils.is_git_dirty(workspace) if is_git else False
    active_lock = locks.active_lock_for_workspace(db_path, workspace) if workspace else None
    workspace_locked = active_lock is not None

    warnings: List[str] = []
    can_rollback = True
    if checkpoint.status == CHECKPOINT_SKIPPED or not target_head:
        can_rollback = False
        warnings.append("no captured git state to roll back to (checkpoint was skipped or empty)")
    elif not is_git:
        can_rollback = False
        warnings.append("workspace is missing or no longer a git repository")

    requires_force = False
    if can_rollback:
        if preexisting_dirty:
            requires_force = True
            warnings.append(
                "workspace had uncommitted changes before the run; reset --hard would also discard them"
            )
        if workspace_locked:
            requires_force = True
            warnings.append(
                f"workspace is held by an active run lock (run #{active_lock.run_id}); "
                "rolling back may disrupt an in-progress run"
            )
        if current_dirty and not preexisting_dirty:
            warnings.append("rollback will discard the current uncommitted changes in the workspace")

    safe = can_rollback and not requires_force
    summary = _plan_summary(can_rollback, safe, requires_force, target_head, checkpoint.git_branch_before)
    return RollbackPlan(
        checkpoint_id=checkpoint.id,
        run_id=checkpoint.run_id,
        workspace_path=workspace,
        status=checkpoint.status,
        mode=ROLLBACK_MODE_RESET,
        target_head=target_head,
        target_branch=checkpoint.git_branch_before,
        current_head=current_head,
        current_branch=current_branch,
        is_git_repo=is_git,
        preexisting_dirty=preexisting_dirty,
        current_dirty=current_dirty,
        workspace_locked=workspace_locked,
        can_rollback=can_rollback,
        requires_force=requires_force,
        safe=safe,
        summary=summary,
        warnings=warnings,
    )


def _plan_summary(can_rollback, safe, requires_force, target_head, target_branch) -> str:
    if not can_rollback:
        return "Rollback is not possible for this checkpoint."
    short = (target_head or "")[:12]
    where = f"{short} (branch {target_branch})" if target_branch else short
    if safe:
        return f"Rollback would run: git reset --hard {where}. Current uncommitted changes will be discarded."
    if requires_force:
        return (
            f"Rollback to {where} is possible but unsafe; it requires force=True because the workspace "
            "has changes not created by the run (or is locked)."
        )
    return f"Rollback would run: git reset --hard {where}."


def rollback_checkpoint(
    db_path: str, checkpoint_id: int, confirm: bool = False, force: bool = False
) -> RollbackResult:
    """Roll the workspace back to a checkpoint's pre-run HEAD via ``git reset --hard``.

    This is the only destructive path. It refuses unless ``confirm`` is True
    (``CheckpointError("not_confirmed")``), refuses when the rollback is unsafe and ``force`` is
    not given (``CheckpointError("unsafe")``), and records a safety-warning artifact immediately
    before the reset. On success the checkpoint is marked ``RESTORED``; a failed Git reset is
    reported (checkpoint ``FAILED``) rather than raised. Never automatic.
    """
    db_path = storage.init_db(db_path)
    checkpoint = _require_checkpoint(db_path, checkpoint_id)
    plan = build_rollback_plan(db_path, checkpoint_id)

    if not plan.can_rollback:
        raise CheckpointError("unsafe", plan.warnings[0] if plan.warnings else "cannot roll back this checkpoint")
    if confirm is not True:
        raise CheckpointError(
            "not_confirmed", "rollback requires explicit confirmation (confirm=true / --confirm)"
        )
    if plan.requires_force and not force:
        detail = "; ".join(w for w in plan.warnings if "discard them" in w or "active run lock" in w)
        raise CheckpointError(
            "unsafe",
            (detail or "workspace state is unsafe for rollback") + " -- pass force=true / --force to override",
        )

    target_head = checkpoint.git_head_before
    # Record a safety-warning artifact *before* the destructive reset (audit trail).
    storage.create_artifact(
        db_path, run_id=checkpoint.run_id, artifact_type=safety.SAFETY_WARNING_ARTIFACT,
        content=(
            f"checkpoint rollback: git reset --hard {target_head} in {checkpoint.workspace_path} "
            f"(checkpoint #{checkpoint.id}, confirm=true, force={str(force).lower()}); "
            "uncommitted changes in the workspace will be discarded"
        ),
    )

    result = git_utils.git_reset_hard(checkpoint.workspace_path, target_head, confirm=True)
    if result.ok:
        storage.mark_checkpoint_restored(db_path, checkpoint.id)
        head_after = git_utils.get_git_head(checkpoint.workspace_path)
        message = f"rolled back to {(target_head or '')[:12]}"
        storage.create_artifact(
            db_path, run_id=checkpoint.run_id, artifact_type=ARTIFACT_CHECKPOINT_ROLLBACK,
            content=f"{message} (branch {checkpoint.git_branch_before}); HEAD is now {head_after}",
        )
        _emit(
            db_path, checkpoint.run_id, EVENT_CHECKPOINT_ROLLED_BACK, message=message,
            payload={"checkpoint_id": checkpoint.id, "head": head_after, "forced": bool(force)},
        )
        return RollbackResult(
            checkpoint_id=checkpoint.id, run_id=checkpoint.run_id, status=CHECKPOINT_RESTORED,
            restored=True, target_head=target_head, git_head_after=head_after, message=message,
        )

    error = (result.stderr or result.stdout or "git reset --hard failed").strip()
    storage.mark_checkpoint_failed(db_path, checkpoint.id, error=error)
    storage.create_artifact(
        db_path, run_id=checkpoint.run_id, artifact_type=ARTIFACT_CHECKPOINT_ROLLBACK,
        content=f"rollback failed: {error}",
    )
    return RollbackResult(
        checkpoint_id=checkpoint.id, run_id=checkpoint.run_id, status=CHECKPOINT_FAILED,
        restored=False, target_head=target_head,
        git_head_after=git_utils.get_git_head(checkpoint.workspace_path), message="rollback failed", error=error,
    )


def _emit(db_path: str, run_id: int, event_type: str, message=None, payload=None) -> None:
    """Emit a best-effort run event (never breaks checkpoint/rollback)."""
    try:
        events.create_event(db_path, run_id, event_type, message=message, payload=payload)
    except Exception:  # noqa: BLE001
        pass
