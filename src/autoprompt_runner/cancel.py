"""Run cancellation vocabulary.

Cancelling a run stops it safely: a queued job is cancelled, a waiting run has its pending
approval rejected, and a running run is marked stopped (with a best-effort termination of
any locally-registered agent subprocess -- see :mod:`autoprompt_runner.processes`). The
run is moved to ``STOPPED``, its workspace lock is released, and a ``cancellation``
artifact is recorded. The orchestration lives in ``RunService.cancel_run``; this module
provides the status constants, artifact-type names, and the result shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .storage import (  # noqa: F401  (re-exported as the cancellation status surface)
    CANCELLATION_COMPLETED,
    CANCELLATION_FAILED,
    CANCELLATION_REQUESTED,
)

CANCELLATION_STATUSES = (CANCELLATION_REQUESTED, CANCELLATION_COMPLETED, CANCELLATION_FAILED)

# Artifact types recorded by the cancellation flow.
CANCELLATION_ARTIFACT = "cancellation"
CANCELLATION_ERROR_ARTIFACT = "cancellation_error"


@dataclass
class CancelResult:
    """The outcome of a :meth:`RunService.cancel_run` call."""

    run_id: int
    run_status: str
    cancelled: bool  # whether the run was moved to STOPPED
    terminated: bool  # whether a locally-registered agent process was terminated
    reason: Optional[str]
    message: str
