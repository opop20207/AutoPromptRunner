"""Approval statuses for the approval gate.

An approval gates the execution of a generated next prompt. By default a run pauses at
WAITING_APPROVAL with a PENDING approval until the user approves or rejects it (see
AGENTS.md, "Prompt Loop Rules"). The persistence functions live in ``storage.py``.
"""

from __future__ import annotations

from enum import Enum
from typing import Set


class ApprovalStatus(str, Enum):
    """The lifecycle states of an approval."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# Statuses that represent a resolved (decided) approval.
DECIDED_STATUSES: Set["ApprovalStatus"] = {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}
