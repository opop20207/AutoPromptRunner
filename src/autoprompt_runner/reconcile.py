"""Stale-state reconciliation for crash / restart recovery.

After an API or worker crash, a machine restart, or an interrupted run, the database can
hold stale state: ``RUNNING`` runs whose process is gone, ``RUNNING`` queue jobs orphaned by
a dead worker, ``ACTIVE`` workspace locks past their expiry, and ``REQUESTED`` cancellations
for runs that already finished. This module detects that state and reconciles it safely.

It is **local-first and non-destructive**: it never deletes files, never runs a Git command,
and never assumes distributed process control. It only flips database rows (run/job/lock/
cancellation status), records reconciliation artifacts/events, and uses the local worker
heartbeat to tell a live worker from a crashed one. Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from . import events, locks, queue, storage
from .state import RunStatus, TERMINAL_STATUSES

# Grace beyond a run's / job's configured timeout before it is considered stale.
GRACE_SECONDS_DEFAULT = 300
# A worker heartbeat not refreshed within this many seconds is treated as crashed.
WORKER_STALE_SECONDS_DEFAULT = 60
_FALLBACK_TIMEOUT_SECONDS = 1800

# Reconciliation artifact type strings (stored via storage.create_artifact as plain strings).
ARTIFACT_RECONCILIATION_REPORT = "reconciliation_report"
ARTIFACT_STALE_RUN_DETECTED = "stale_run_detected"
ARTIFACT_STALE_LOCK_EXPIRED = "stale_lock_expired"
ARTIFACT_STALE_QUEUE_JOB_FAILED = "stale_queue_job_failed"

_RUNNING = RunStatus.RUNNING.value
_FAILED = RunStatus.FAILED.value
_STOPPED = RunStatus.STOPPED.value


@dataclass
class ReconciliationAction:
    kind: str  # "run" | "queue_job" | "lock" | "cancellation" | "worker"
    target_id: int
    run_id: Optional[int]
    action: str
    reason: str


@dataclass
class ReconciliationReport:
    dry_run: bool
    generated_at: str
    stale_runs: int = 0
    stale_queue_jobs: int = 0
    stale_locks: int = 0
    orphaned_cancellations: int = 0
    stale_workers: int = 0
    actions: List[ReconciliationAction] = field(default_factory=list)


@dataclass
class SystemStatus:
    active_workers: int
    stale_workers: int
    queued_jobs: int
    running_jobs: int
    active_locks: int
    stale_locks: int
    stale_runs: int
    generated_at: str


# -- time helpers ------------------------------------------------------------


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _age_seconds(ts: Optional[str], now: datetime) -> Optional[float]:
    parsed = _parse(ts)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds()


def _has_live_worker(db_path: str, now: datetime, worker_stale_seconds: int) -> bool:
    for hb in storage.get_active_worker_heartbeats(db_path):
        age = _age_seconds(hb.updated_at, now)
        if age is not None and age <= worker_stale_seconds:
            return True
    return False


# -- detection (pure: never modifies the database) ---------------------------


def detect_stale_runs(
    db_path: str, now: Optional[datetime] = None, grace_seconds: int = GRACE_SECONDS_DEFAULT
) -> List[Tuple[object, str]]:
    """Return ``(run, reason)`` for RUNNING runs older than their timeout + grace.

    WAITING_APPROVAL and terminal runs are never returned. ``reason`` is "cancellation
    requested" when a cancellation is pending (-> STOPPED), else "worker interrupted" (-> FAILED).
    """
    moment = _now(now)
    stale: List[Tuple[object, str]] = []
    for run in storage.list_runs_by_status(db_path, _RUNNING):
        timeout = run.timeout_seconds or _FALLBACK_TIMEOUT_SECONDS
        age = _age_seconds(run.created_at, moment)
        if age is None or age <= timeout + grace_seconds:
            continue
        cancellation = storage.get_cancellation_for_run(db_path, run.id)
        if cancellation is not None and cancellation.status == storage.CANCELLATION_REQUESTED:
            stale.append((run, "cancellation requested"))
        else:
            stale.append((run, "worker interrupted"))
    return stale


def detect_stale_queue_jobs(
    db_path: str,
    now: Optional[datetime] = None,
    grace_seconds: int = GRACE_SECONDS_DEFAULT,
    worker_stale_seconds: int = WORKER_STALE_SECONDS_DEFAULT,
) -> List[object]:
    """Return RUNNING queue jobs that are orphaned: no live worker and older than timeout + grace."""
    moment = _now(now)
    if _has_live_worker(db_path, moment, worker_stale_seconds):
        return []  # a worker is alive; assume it owns the RUNNING jobs
    stale: List[object] = []
    for job in storage.list_queue_jobs_by_status(db_path, storage.QUEUE_RUNNING):
        run = storage.get_run(db_path, job.run_id)
        timeout = (run.timeout_seconds if run and run.timeout_seconds else _FALLBACK_TIMEOUT_SECONDS)
        age = _age_seconds(job.started_at or job.created_at, moment)
        if age is not None and age > timeout + grace_seconds:
            stale.append(job)
    return stale


def detect_stale_locks(db_path: str, now: Optional[datetime] = None) -> List[Tuple[object, str]]:
    """Return ``(lock, reason)`` for ACTIVE locks past expiry or held by a terminal run."""
    moment = _now(now)
    stale: List[Tuple[object, str]] = []
    for lock in storage.list_active_locks(db_path):
        age_past = _age_seconds(lock.expires_at, moment)
        if age_past is not None and age_past > 0:
            stale.append((lock, "expired"))
            continue
        run = storage.get_run(db_path, lock.run_id)
        if run is not None and run.status in TERMINAL_STATUSES:
            stale.append((lock, "held by terminal run"))
    return stale


def detect_orphaned_cancellations(db_path: str) -> List[Tuple[object, str]]:
    """Return ``(cancellation, reason)`` for REQUESTED cancellations of terminal/missing runs."""
    out: List[Tuple[object, str]] = []
    for cancellation in storage.list_cancellations_by_status(db_path, storage.CANCELLATION_REQUESTED):
        run = storage.get_run(db_path, cancellation.run_id)
        if run is None:
            out.append((cancellation, "run missing"))
        elif run.status in TERMINAL_STATUSES:
            out.append((cancellation, "run already terminal"))
    return out


def detect_stale_workers(
    db_path: str, now: Optional[datetime] = None, worker_stale_seconds: int = WORKER_STALE_SECONDS_DEFAULT
) -> List[object]:
    """Return ACTIVE worker heartbeats not refreshed within the staleness window."""
    moment = _now(now)
    before = (moment - timedelta(seconds=worker_stale_seconds)).isoformat()
    return storage.detect_stale_worker_heartbeats(db_path, before)


# -- status + report ---------------------------------------------------------


def build_system_status(
    db_path: str, now: Optional[datetime] = None, worker_stale_seconds: int = WORKER_STALE_SECONDS_DEFAULT
) -> SystemStatus:
    """Return a compact snapshot of workers / jobs / locks / stale state (read-only)."""
    moment = _now(now)
    active = storage.get_active_worker_heartbeats(db_path)
    stale_workers = [hb for hb in active if (_age_seconds(hb.updated_at, moment) or 0) > worker_stale_seconds]
    return SystemStatus(
        active_workers=len(active) - len(stale_workers),
        stale_workers=len(stale_workers),
        queued_jobs=len(storage.list_queue_jobs_by_status(db_path, storage.QUEUE_QUEUED)),
        running_jobs=len(storage.list_queue_jobs_by_status(db_path, storage.QUEUE_RUNNING)),
        active_locks=len(storage.list_active_locks(db_path)),
        stale_locks=len(detect_stale_locks(db_path, moment)),
        stale_runs=len(detect_stale_runs(db_path, moment)),
        generated_at=moment.isoformat(),
    )


def build_reconciliation_report(dry_run: bool, generated_at: str, actions: List[ReconciliationAction]) -> ReconciliationReport:
    """Assemble a report from the actions taken (or that would be taken in a dry run)."""
    return ReconciliationReport(
        dry_run=dry_run,
        generated_at=generated_at,
        stale_runs=sum(1 for a in actions if a.kind == "run"),
        stale_queue_jobs=sum(1 for a in actions if a.kind == "queue_job"),
        stale_locks=sum(1 for a in actions if a.kind == "lock"),
        orphaned_cancellations=sum(1 for a in actions if a.kind == "cancellation"),
        stale_workers=sum(1 for a in actions if a.kind == "worker"),
        actions=actions,
    )


# -- reconciliation (acts unless dry_run) ------------------------------------


def reconcile_stale_state(
    db_path: str,
    dry_run: bool = False,
    now: Optional[datetime] = None,
    grace_seconds: int = GRACE_SECONDS_DEFAULT,
    worker_stale_seconds: int = WORKER_STALE_SECONDS_DEFAULT,
) -> ReconciliationReport:
    """Detect and (unless ``dry_run``) fix stale state. Returns a compact report.

    Non-destructive: only database rows are changed and reconciliation artifacts/events are
    recorded. No files are deleted and no Git command is run.
    """
    db_path = storage.init_db(db_path)
    moment = _now(now)
    now_iso = moment.isoformat()
    actions: List[ReconciliationAction] = []

    if not dry_run:
        events.create_event(db_path, events.SYSTEM_RUN_ID, events.RECONCILIATION_STARTED, message="reconciliation started")

    # 1) Stale RUNNING runs -> FAILED (or STOPPED if a cancellation was requested).
    for run, reason in detect_stale_runs(db_path, moment, grace_seconds):
        target = _STOPPED if reason == "cancellation requested" else _FAILED
        actions.append(ReconciliationAction("run", run.id, run.id, f"mark {target}", reason))
        if not dry_run:
            storage.update_run_status(db_path, run.id, target, finished_at=now_iso)
            storage.create_artifact(db_path, run.id, ARTIFACT_STALE_RUN_DETECTED, content=reason)
            storage.release_run_lock(db_path, run.id)  # free the workspace (no files touched)
            events.create_event(db_path, run.id, events.STALE_RUN_FAILED, message=reason, payload={"status": target})

    # 2) Stale RUNNING queue jobs (worker crashed) -> FAILED.
    for job in detect_stale_queue_jobs(db_path, moment, grace_seconds, worker_stale_seconds):
        actions.append(ReconciliationAction("queue_job", job.id, job.run_id, "mark FAILED", "stale RUNNING job"))
        if not dry_run:
            queue.fail_stale_job(db_path, job.id)
            if storage.get_run(db_path, job.run_id) is not None:
                storage.create_artifact(db_path, job.run_id, ARTIFACT_STALE_QUEUE_JOB_FAILED, content="stale RUNNING job")
            events.create_event(db_path, job.run_id, events.STALE_JOB_FAILED, message="stale RUNNING job")

    # 3) Stale workspace locks -> EXPIRED (only flips the row; never deletes files).
    for lock, reason in detect_stale_locks(db_path, moment):
        actions.append(ReconciliationAction("lock", lock.id, lock.run_id, "expire", reason))
        if not dry_run:
            storage.expire_lock_by_id(db_path, lock.id)
            if storage.get_run(db_path, lock.run_id) is not None:
                storage.create_artifact(db_path, lock.run_id, ARTIFACT_STALE_LOCK_EXPIRED, content=reason)
            events.create_event(db_path, lock.run_id, events.STALE_LOCK_EXPIRED, message=reason)

    # 4) Orphaned cancellations: terminal run -> COMPLETED; missing run -> failed with a warning.
    for cancellation, reason in detect_orphaned_cancellations(db_path):
        action = "complete" if reason == "run already terminal" else "warn (run missing)"
        actions.append(ReconciliationAction("cancellation", cancellation.id, cancellation.run_id, action, reason))
        if not dry_run:
            if reason == "run already terminal":
                storage.complete_run_cancellation(db_path, cancellation.id)
            else:
                storage.fail_run_cancellation(db_path, cancellation.id, "run missing; cancellation could not be completed")

    # 5) Stale worker heartbeats -> STOPPED.
    for hb in detect_stale_workers(db_path, moment, worker_stale_seconds):
        actions.append(ReconciliationAction("worker", hb.id, None, "mark STOPPED (stale)", "no recent heartbeat"))
        if not dry_run:
            storage.stop_worker_heartbeat(db_path, hb.id)

    report = build_reconciliation_report(dry_run, now_iso, actions)

    if not dry_run:
        # Tag each affected run with a compact reconciliation report artifact.
        summary = (
            f"reconciled: {report.stale_runs} run(s), {report.stale_queue_jobs} job(s), "
            f"{report.stale_locks} lock(s), {report.orphaned_cancellations} cancellation(s)"
        )
        for run_id in {a.run_id for a in actions if a.run_id}:
            if storage.get_run(db_path, run_id) is not None:
                storage.create_artifact(db_path, run_id, ARTIFACT_RECONCILIATION_REPORT, content=summary)
        events.create_event(
            db_path, events.SYSTEM_RUN_ID, events.RECONCILIATION_FINISHED, message=summary,
            payload={
                "stale_runs": report.stale_runs, "stale_queue_jobs": report.stale_queue_jobs,
                "stale_locks": report.stale_locks, "orphaned_cancellations": report.orphaned_cancellations,
                "stale_workers": report.stale_workers,
            },
        )
    return report
