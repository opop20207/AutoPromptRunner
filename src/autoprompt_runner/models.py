"""Core data structures for AutoPromptRunner.

These dataclasses are intentionally simple containers shared between the CLI, the
runners, the storage layer, and (later) the orchestrator. They carry no behavior.
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
    """A request to execute one prompt against a provider under explicit limits.

    ``require_approval`` reflects the default approval gate; when it is ``False`` the
    caller has explicitly opted into auto-run. The loop bound ``max_loops`` is carried
    here but not yet exercised by the single-step run.
    """

    prompt: str
    provider: str = "mock"
    max_loops: int = 1
    require_approval: bool = True


@dataclass
class RunReport:
    """Compact, user-facing summary of a run.

    ``status`` is one of the terminal labels used by the CLI report (for example
    ``"DONE"`` or ``"FAILED"``). ``result`` and ``next_prompt`` may be ``None`` when a
    run produced no execution result or no follow-up prompt.
    """

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
