"""Core data structures for AutoPromptRunner.

These dataclasses are intentionally simple containers shared between the CLI, the
runners, the storage layer, and the services. They carry no behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


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

    ``workspace`` is the directory a real provider (such as claude-code) runs in;
    ``timeout_seconds`` bounds a provider subprocess.
    """

    prompt: str
    provider: str = "mock"
    max_loops: int = 1
    require_approval: bool = True
    workspace: Optional[str] = None
    timeout_seconds: int = 1800


@dataclass
class RunReport:
    """Compact, user-facing summary of a single run step."""

    status: str
    provider: str
    prompt: str
    result: Optional[AgentResult] = None
    next_prompt: Optional[str] = None


@dataclass
class Project:
    """A reusable project profile of run settings, persisted in ``projects``.

    ``require_approval`` is stored as an integer (0/1) in SQLite but exposed here as a
    ``bool``. A project lets the user avoid passing workspace/provider/limits on every
    run (see the ``--project`` flag and the default project).
    """

    id: int
    name: str
    repo_path: str
    default_provider: str
    default_max_loops: int
    require_approval: bool
    timeout_seconds: int
    created_at: str
    updated_at: str


@dataclass
class Template:
    """A reusable prompt template, persisted in the ``templates`` table.

    ``body`` is plain text that may contain ``{{placeholder}}`` tokens (see
    ``autoprompt_runner.templates``). ``tags`` are simple labels, stored as a single
    text column in SQLite but exposed here as a list. Templates carry no executable
    content -- rendering only substitutes known placeholders.
    """

    id: int
    name: str
    description: str
    body: str
    tags: List[str]
    created_at: str
    updated_at: Optional[str] = None


@dataclass
class Worktree:
    """A Git worktree profile for an isolated parallel session, in ``worktrees``.

    Each worktree binds a project to a checked-out branch in its own directory on disk so
    parallel agent sessions never share one working tree. ``status`` is one of
    ``ACTIVE`` / ``LOCKED`` / ``ARCHIVED`` (see ``autoprompt_runner.worktrees``); the row
    only tracks the worktree -- the files on disk are managed solely through
    ``git worktree`` commands.
    """

    id: int
    project_id: int
    name: str
    branch: str
    path: str
    base_branch: Optional[str]
    status: str
    created_at: str
    updated_at: str


@dataclass
class RunLock:
    """A workspace execution lock, persisted in ``run_locks``.

    Guards against two active runs executing in the same workspace at once (which would
    corrupt edits and mix diffs). ``status`` is ``ACTIVE`` / ``RELEASED`` / ``EXPIRED``
    (see ``autoprompt_runner.locks``). ``workspace_path`` is stored normalized so
    differently-written paths to the same directory compare equal. ``expires_at`` lets a
    lock be reclaimed if a process dies before releasing it.
    """

    id: int
    workspace_path: str
    run_id: int
    status: str
    owner: Optional[str]
    created_at: str
    updated_at: str
    expires_at: Optional[str] = None


@dataclass
class QueueJob:
    """A queued run job, persisted in ``run_queue``.

    Lets the API create a run quickly and have a local background worker execute it later
    (Claude Code / Codex runs can be slow). ``status`` is ``QUEUED`` / ``RUNNING`` /
    ``DONE`` / ``FAILED`` / ``CANCELLED`` (see ``autoprompt_runner.queue``). Lower
    ``priority`` numbers run first; ties break by ``created_at``. ``attempts`` is bounded
    by ``max_attempts`` (no automatic retry beyond it).
    """

    id: int
    run_id: int
    status: str
    priority: int
    attempts: int
    max_attempts: int
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    last_error: Optional[str] = None


@dataclass
class RunCancellation:
    """A cancellation request for a run, persisted in ``run_cancellations``.

    Records that a user asked to stop a queued / running / waiting run. ``status`` is
    ``REQUESTED`` / ``COMPLETED`` / ``FAILED`` (see ``autoprompt_runner.cancel``).
    Cancelling a queued or waiting run is deterministic; force-stopping an already-running
    external process is best-effort and only works inside the worker that launched it.
    """

    id: int
    run_id: int
    status: str
    reason: Optional[str]
    requested_at: str
    completed_at: Optional[str] = None
    error: Optional[str] = None


@dataclass
class StoredRun:
    """A run row as persisted in the ``runs`` table.

    ``require_approval`` is stored as an integer (0/1) in SQLite but exposed here as a
    ``bool``. ``project_id``, ``finished_at``, ``workspace``, and ``timeout_seconds``
    are ``None`` when not set.
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
    workspace: Optional[str] = None
    timeout_seconds: Optional[int] = None


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
class Artifact:
    """An artifact row as persisted in the ``artifacts`` table.

    Records a piece of a step's context: the read-only Git state around the step
    (status/diff/changed files) or the runner's stdout/stderr. ``content`` holds inline
    text; ``path`` is reserved for artifacts stored on disk (unused for now).
    """

    id: int
    run_id: int
    step_id: Optional[int]
    type: str
    content: Optional[str]
    path: Optional[str]
    created_at: str


@dataclass
class PromptGenerationContext:
    """Everything the PromptGenerator needs to choose the next prompt.

    Combines the run intent (root/previous prompt, loop bounds, approval mode), the
    runner result (stdout/stderr/exit_code), and the read-only Git signal
    (changed_files, git_diff_stat) captured for the step.
    """

    root_prompt: str
    previous_prompt: str
    exit_code: int
    loop_index: int
    max_loops: int
    stdout: str = ""
    stderr: str = ""
    changed_files: List[str] = field(default_factory=list)
    git_diff_stat: str = ""
    provider: str = "mock"
    workspace: Optional[str] = None
    require_approval: bool = True


@dataclass
class NextPrompt:
    """A generated next prompt and how it was derived.

    ``kind`` labels the rule that produced it (for example ``"continue"``, ``"fix"``,
    ``"fix_tests"``, ``"wrapup"``). ``loop_index`` is the index of the step this prompt
    would drive next.
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
    workspace: Optional[str] = None
    timeout_seconds: int = 1800


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
