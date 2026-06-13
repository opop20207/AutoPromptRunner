"""Core data structures for AutoPromptRunner.

These dataclasses are intentionally simple containers shared between the CLI, the
runners, and (later) the orchestrator and persistence layers. They carry no behavior.
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
    here but not yet exercised by the single-step skeleton.
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
