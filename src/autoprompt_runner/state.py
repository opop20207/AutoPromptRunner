"""Run status values and the transition rules between them.

The practical subset of the PROJECT.md state machine used by this project: CREATED,
RUNNING, WAITING_APPROVAL, DONE, FAILED, STOPPED. Transitions are deliberately
explicit and small; anything not listed is rejected with a ``ValueError`` so an
invalid status change can never be written to the database.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Set, Union


class RunStatus(str, Enum):
    """The run statuses persisted by this project."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    DONE = "DONE"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


# Terminal statuses have no outgoing transitions and stamp ``finished_at``.
TERMINAL_STATUSES: Set["RunStatus"] = {RunStatus.DONE, RunStatus.FAILED, RunStatus.STOPPED}

# Explicit allow-list of legal transitions. Empty set => terminal (no exit).
_ALLOWED_TRANSITIONS: Dict["RunStatus", Set["RunStatus"]] = {
    RunStatus.CREATED: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.STOPPED},
    RunStatus.RUNNING: {
        RunStatus.WAITING_APPROVAL,
        RunStatus.DONE,
        RunStatus.FAILED,
        RunStatus.STOPPED,
    },
    RunStatus.WAITING_APPROVAL: {
        RunStatus.RUNNING,
        RunStatus.DONE,
        RunStatus.FAILED,
        RunStatus.STOPPED,
    },
    RunStatus.DONE: set(),
    RunStatus.FAILED: set(),
    RunStatus.STOPPED: set(),
}

StatusLike = Union["RunStatus", str]


def _coerce(status: StatusLike) -> "RunStatus":
    """Return a RunStatus for a RunStatus or its string value; raise ValueError otherwise."""
    if isinstance(status, RunStatus):
        return status
    try:
        return RunStatus(status)
    except ValueError as exc:
        raise ValueError(f"unknown run status: {status!r}") from exc


def validate_status_transition(from_status: StatusLike, to_status: StatusLike) -> "RunStatus":
    """Validate a status change and return the target ``RunStatus``.

    Both arguments accept a ``RunStatus`` or its string value. The transition is valid
    only if explicitly listed in the allow-list above; otherwise a ``ValueError`` is
    raised. An unknown status value also raises ``ValueError``.
    """
    src = _coerce(from_status)
    dst = _coerce(to_status)
    if dst not in _ALLOWED_TRANSITIONS[src]:
        raise ValueError(f"invalid status transition: {src.value} -> {dst.value}")
    return dst
