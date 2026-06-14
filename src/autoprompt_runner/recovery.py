"""Failure recovery workflow.

When a run ends FAILED, this module builds a focused, **rule-based** recovery prompt from
the run's *stored* failure context (the failed step's prompt and stdout/stderr previews,
exit code, changed files, diff stat, and safety warnings) and records it as a recovery
attempt. The user can approve / reject it, and execute it -- which creates a **new linked
run** that reuses the source run's provider / workspace / timeout / approval settings and
obeys the same safety, lock, queue, and approval behavior. The original run's records are
never mutated; only the recovery linking metadata is stored.

It calls no external AI APIs and reads no workspace files: the recovery prompt is generated
by ``services.prompt_generator.build_recovery_prompt`` from stored content only, and
previews are compact so no huge artifact content (or secret-file content) is echoed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from . import queue, safety, storage
from .artifacts import ArtifactType
from .services.prompt_generator import build_recovery_prompt as _generate_recovery_prompt
from .services.run_service import RECOVERABLE_STATUSES, RunService, RunServiceError

# Recovery attempt statuses.
RECOVERY_PROPOSED = "PROPOSED"
RECOVERY_APPROVED = "APPROVED"
RECOVERY_REJECTED = "REJECTED"
RECOVERY_EXECUTED = "EXECUTED"
RECOVERY_FAILED = "FAILED"

# Compact preview cap for stored stdout/stderr shown in the failure context.
_PREVIEW = 300
_DIFF_STAT_CAP = 2000


class RecoveryError(Exception):
    """Raised for recovery problems.

    ``kind`` is ``"not_found"`` (run/recovery missing), ``"not_failed"`` (source run is not
    FAILED), ``"rejected"`` (a rejected recovery cannot execute), or ``"executed"`` (already
    executed). Callers map it to a CLI exit code or an HTTP status (404 / 400 / 409).
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class FailureContext:
    run_id: int
    root_prompt: str
    failed_step_id: Optional[int]
    failed_step_prompt: str
    stdout_preview: str
    stderr_preview: str
    exit_code: Optional[int]
    changed_files: List[str]
    git_diff_stat: str
    safety_warnings: List[str]
    provider: str
    workspace: Optional[str]
    loop_index: Optional[int]


@dataclass
class RecoveryExecutionResult:
    attempt: "storage.RecoveryAttempt"
    recovery_run_id: int
    run_status: Optional[str]
    queued: bool
    error: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview(text: Optional[str], limit: int = _PREVIEW) -> str:
    norm = " ".join((text or "").split())
    return norm if len(norm) <= limit else norm[:limit] + "…"


def find_failed_step(db_path: str, run_id: int):
    """Return the step that failed for ``run_id`` (status FAILED or non-zero exit), or None.

    The prompt loop stops on the first failing step, so the failed step is the most recent
    failing one; if no step explicitly failed, the last step (if any) is returned.
    """
    steps = storage.get_steps_for_run(db_path, run_id)
    failed = [
        s for s in steps if (s.status or "").upper() == "FAILED" or (s.exit_code is not None and s.exit_code != 0)
    ]
    if failed:
        return failed[-1]
    return steps[-1] if steps else None


def build_failure_context(db_path: str, run_id: int) -> FailureContext:
    """Assemble the stored failure context for ``run_id`` (no disk reads, compact previews)."""
    run = storage.get_run(db_path, run_id)
    if run is None:
        raise RecoveryError("not_found", f"run {run_id} not found")
    step = find_failed_step(db_path, run_id)

    changed_files: List[str] = []
    latest_changed = storage.get_latest_artifact_by_type(db_path, run_id, ArtifactType.CHANGED_FILES.value)
    if latest_changed and latest_changed.content:
        changed_files = [line.strip() for line in latest_changed.content.splitlines() if line.strip()]

    diff_stat = ""
    latest_stat = storage.get_latest_artifact_by_type(db_path, run_id, ArtifactType.GIT_DIFF_STAT.value)
    if latest_stat and latest_stat.content:
        diff_stat = latest_stat.content[:_DIFF_STAT_CAP]

    warnings = [
        a.content
        for a in storage.list_artifacts_for_run(db_path, run_id, safety.SAFETY_WARNING_ARTIFACT)
        if a.content
    ]

    return FailureContext(
        run_id=run_id,
        root_prompt=run.root_prompt,
        failed_step_id=step.id if step else None,
        failed_step_prompt=step.prompt if step else run.root_prompt,
        stdout_preview=_preview(step.stdout) if step else "",
        stderr_preview=_preview(step.stderr) if step else "",
        exit_code=step.exit_code if step else None,
        changed_files=changed_files,
        git_diff_stat=diff_stat,
        safety_warnings=warnings,
        provider=run.provider,
        workspace=run.workspace,
        loop_index=step.loop_index if step else None,
    )


def build_recovery_prompt(context: FailureContext) -> str:
    """Build the rule-based recovery prompt from a failure context (delegates to the generator)."""
    return _generate_recovery_prompt(
        root_prompt=context.root_prompt,
        failed_prompt=context.failed_step_prompt,
        stderr=context.stderr_preview,
        stdout=context.stdout_preview,
        exit_code=context.exit_code,
        changed_files=context.changed_files,
        diff_stat=context.git_diff_stat,
    )


def propose_recovery(db_path: str, run_id: int, reason: Optional[str] = None):
    """Create a PROPOSED recovery attempt for a FAILED run. Raises if the run is not FAILED."""
    run = storage.get_run(db_path, run_id)
    if run is None:
        raise RecoveryError("not_found", f"run {run_id} not found")
    if run.status not in RECOVERABLE_STATUSES:
        raise RecoveryError(
            "not_failed", f"run {run_id} is {run.status}; only FAILED runs can be recovered"
        )
    context = build_failure_context(db_path, run_id)
    prompt = build_recovery_prompt(context)
    recovery_id = storage.create_recovery_attempt(
        db_path,
        source_run_id=run_id,
        recovery_prompt=prompt,
        failed_step_id=context.failed_step_id,
        reason=reason,
        status=RECOVERY_PROPOSED,
    )
    return storage.get_recovery_attempt(db_path, recovery_id)


def _require_recovery(db_path: str, recovery_id: int):
    recovery = storage.get_recovery_attempt(db_path, recovery_id)
    if recovery is None:
        raise RecoveryError("not_found", f"recovery {recovery_id} not found")
    return recovery


def approve_recovery(db_path: str, recovery_id: int):
    """Mark a recovery attempt APPROVED (does not execute it)."""
    recovery = _require_recovery(db_path, recovery_id)
    if recovery.status == RECOVERY_EXECUTED:
        raise RecoveryError("executed", f"recovery {recovery_id} was already executed")
    storage.update_recovery_status(db_path, recovery_id, RECOVERY_APPROVED, decided_at=_now_iso())
    return storage.get_recovery_attempt(db_path, recovery_id)


def reject_recovery(db_path: str, recovery_id: int, reason: Optional[str] = None):
    """Mark a recovery attempt REJECTED."""
    recovery = _require_recovery(db_path, recovery_id)
    if recovery.status == RECOVERY_EXECUTED:
        raise RecoveryError("executed", f"recovery {recovery_id} was already executed")
    storage.update_recovery_status(
        db_path, recovery_id, RECOVERY_REJECTED, decided_at=_now_iso(), reason=reason if reason else None
    )
    return storage.get_recovery_attempt(db_path, recovery_id)


def execute_recovery(
    db_path: str, recovery_id: int, queued: bool = False, service: Optional[RunService] = None
) -> RecoveryExecutionResult:
    """Execute an approved/proposed recovery: create a new linked run and run or queue it.

    The recovery run reuses the source run's settings (provider / workspace / timeout /
    max_loops / approval / project) and obeys the usual safety, lock, queue, and approval
    behavior. ``recovery_run_id`` is linked immediately (even when queued). On a pre-execution
    failure (lock conflict, safety block, ...) the attempt is marked FAILED and the result
    carries the error; a rejected recovery cannot be executed.
    """
    recovery = _require_recovery(db_path, recovery_id)
    if recovery.status == RECOVERY_REJECTED:
        raise RecoveryError("rejected", f"recovery {recovery_id} was rejected; cannot execute")
    if recovery.status == RECOVERY_EXECUTED:
        raise RecoveryError("executed", f"recovery {recovery_id} was already executed")

    source = storage.get_run(db_path, recovery.source_run_id)
    if source is None:
        raise RecoveryError("not_found", f"source run {recovery.source_run_id} not found")

    service = service or RunService(db_path)
    run_id = service.create_run_only_like(source, recovery.recovery_prompt)
    storage.attach_recovery_run(db_path, recovery_id, run_id)  # link immediately

    if queued:
        queue.enqueue(db_path, run_id)
        storage.update_recovery_status(db_path, recovery_id, RECOVERY_EXECUTED, executed_at=_now_iso())
        run = storage.get_run(db_path, run_id)
        return RecoveryExecutionResult(
            attempt=storage.get_recovery_attempt(db_path, recovery_id),
            recovery_run_id=run_id,
            run_status=run.status if run else None,
            queued=True,
        )

    try:
        report = service.execute_run_step(run_id)
        storage.update_recovery_status(db_path, recovery_id, RECOVERY_EXECUTED, executed_at=_now_iso())
        return RecoveryExecutionResult(
            attempt=storage.get_recovery_attempt(db_path, recovery_id),
            recovery_run_id=run_id,
            run_status=report.run_status,
            queued=False,
        )
    except RunServiceError as exc:
        storage.update_recovery_status(db_path, recovery_id, RECOVERY_FAILED, executed_at=_now_iso())
        run = storage.get_run(db_path, run_id)
        return RecoveryExecutionResult(
            attempt=storage.get_recovery_attempt(db_path, recovery_id),
            recovery_run_id=run_id,
            run_status=run.status if run else None,
            queued=False,
            error=str(exc),
        )


def list_recoveries_for_run(db_path: str, run_id: int):
    """Return all recovery attempts for ``run_id`` (newest first)."""
    return list(reversed(storage.list_recoveries_for_run(db_path, run_id)))


def list_recoveries(db_path: str, limit: int = 50):
    """Return up to ``limit`` recovery attempts across all runs, newest first."""
    return storage.list_recoveries(db_path, limit=limit)
