"""Core data structures for AutoPromptRunner.

These dataclasses are intentionally simple containers shared between the CLI, the
runners, the storage layer, and the services. They carry no behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentResult:
    """Captured outcome of a single agent execution.

    Mirrors the fields every runner must record (see AGENTS.md, "Agent Runner Rules"):
    stdout, stderr, exit code, and the start/finish timestamps. ``started_at`` and
    ``finished_at`` are ISO 8601 strings.
    """

    stdout: str
    stderr: str
    exit_code: int
    started_at: str
    finished_at: str


@dataclass
class RunRequest:
    """A request to execute one prompt against a provider under explicit limits."""

    prompt: str
    provider: str = "mock"
    max_loops: int = 1
    require_approval: bool = True


@dataclass
class RunReport:
    """Compact, user-facing summary of a single run step."""

    status: str
    provider: str
    prompt: str
    result: Optional[AgentResult] = None
    next_prompt: Optional[str] = None


@dataclass
class StoredRun:
    """A run row as persisted in the ``runs`` table.

    ``require_approval`` is stored as an integer (0/1) in SQLite but exposed here as a
    ``bool``. ``project_id`` and ``finished_at`` are ``None`` until set.
    """

    id: int
    project_id: Optional[int]
    root_prompt: str
    provider: str
    status: str
    max_loops: int
    require_approval: bool
    created_at: str
    finished_at: Optional[str] = None


@dataclass
class StoredStep:
    """A step row as persisted in the ``steps`` table.

    Captures the per-execution fields required by AGENTS.md: stdout, stderr,
    exit_code, started_at, and finished_at, plus the generated ``next_prompt``.
    """

    id: int
    run_id: int
    loop_index: int
    prompt: str
    status: str
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    next_prompt: Optional[str] = None


@dataclass
class Approval:
    """An approval row as persisted in the ``approvals`` table.

    Gates the execution of ``next_prompt`` for a run. ``decided_at`` is ``None`` while
    the approval is still PENDING.
    """

    id: int
    run_id: int
    step_id: int
    next_prompt: str
    status: str
    created_at: str
    decided_at: Optional[str] = None


@dataclass
class NextPrompt:
    """A generated next prompt and how it was derived.

    ``kind`` is ``"continue"`` when the previous step succeeded or ``"fix"`` when it
    failed. ``loop_index`` is the index of the step this prompt would drive next.
    """

    prompt: str
    kind: str
    loop_index: int


@dataclass
class StepExecutionRequest:
    """A request to execute one loop step against a provider."""

    run_id: int
    prompt: str
    provider: str
    loop_index: int


@dataclass
class StepExecutionReport:
    """Compact result of executing (or advancing) a run, returned by RunService.

    ``run_status`` is the run's status after the action. ``next_prompt`` and
    ``approval_id`` are populated when the run paused at WAITING_APPROVAL.
    """

    run_id: int
    run_status: str
    loop_index: int
    provider: str
    step_id: Optional[int] = None
    exit_code: Optional[int] = None
    next_prompt: Optional[str] = None
    approval_id: Optional[int] = None
    message: Optional[str] = None
