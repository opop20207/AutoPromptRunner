"""Local run queue.

Queues runs so the API can create a run quickly and return, while a local background
worker (:mod:`autoprompt_runner.worker`) executes it later -- Claude Code / Codex runs can
take a long time. This is a **local SQLite-backed queue for a single machine**, not a
distributed queue or message broker.

The persistence lives in :mod:`autoprompt_runner.storage`; this module owns the
claim/complete/cancel policy and re-exports the queue status constants.
"""

from __future__ import annotations

from typing import Optional

from . import storage
from .models import QueueJob
from .storage import (  # noqa: F401  (re-exported as the queue status surface)
    QUEUE_CANCELLED,
    QUEUE_DONE,
    QUEUE_FAILED,
    QUEUE_QUEUED,
    QUEUE_RUNNING,
)

QUEUE_STATUSES = (QUEUE_QUEUED, QUEUE_RUNNING, QUEUE_DONE, QUEUE_FAILED, QUEUE_CANCELLED)

# Outcomes of :func:`cancel`, used by the CLI/API to pick a status code / message.
CANCEL_CANCELLED = "cancelled"
CANCEL_RUNNING = "running"
CANCEL_NOT_FOUND = "not_found"
CANCEL_NOT_ACTIVE = "not_active"


def enqueue(db_path: str, run_id: int, priority: int = 100, max_attempts: int = 1) -> int:
    """Enqueue a run as a QUEUED job and return the job id (raises if already active)."""
    return storage.enqueue_run(db_path, run_id, priority=priority, max_attempts=max_attempts)


def claim_next_job(db_path: str) -> Optional[QueueJob]:
    """Claim the next queued job (marking it RUNNING) or return ``None`` if the queue is empty.

    Returns the claimed job (its ``id`` and ``run_id`` are what the worker needs).
    """
    job = storage.get_next_queued_job(db_path)
    if job is None:
        return None
    storage.mark_job_running(db_path, job.id)
    return job


def complete_job(db_path: str, job_id: int) -> None:
    """Mark a claimed job DONE (the run executed to a terminal/approval state)."""
    storage.mark_job_done(db_path, job_id)


def fail_job(db_path: str, job_id: int, error: object) -> None:
    """Mark a claimed job FAILED and record the error message."""
    storage.mark_job_failed(db_path, job_id, str(error))


def cancel(db_path: str, run_id: int) -> str:
    """Cancel a run's queued job. Returns one of the ``CANCEL_*`` outcome constants.

    A RUNNING job is not cancelled (process cancellation is not implemented yet); a
    QUEUED job is moved to CANCELLED; anything else is reported as not-found/not-active.
    """
    job = storage.get_job_by_run_id(db_path, run_id)
    if job is None:
        return CANCEL_NOT_FOUND
    if job.status == QUEUE_RUNNING:
        return CANCEL_RUNNING
    if job.status != QUEUE_QUEUED:
        return CANCEL_NOT_ACTIVE
    storage.cancel_job(db_path, run_id)
    return CANCEL_CANCELLED
