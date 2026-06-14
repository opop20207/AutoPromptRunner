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
class ProviderProfile:
    """A configurable provider profile, persisted in the ``provider_profiles`` table.

    Lets a user configure how a provider is invoked (the ``command`` executable, a default
    ``default_timeout_seconds``, and space-separated ``default_args``) without hardcoding it,
    and have several profiles for one ``type`` (for example a ``claude-fast`` profile of type
    ``claude-code``). ``enabled`` is stored as an integer (0/1) in SQLite but exposed here as
    a ``bool``. Profiles never store secrets -- only non-secret command/argument settings.
    """

    id: int
    name: str
    type: str
    command: str
    default_timeout_seconds: int
    default_args: Optional[str]
    enabled: bool
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
class RecoveryAttempt:
    """A failure-recovery attempt for a FAILED run, persisted in ``recovery_attempts``.

    Records a focused, rule-generated recovery prompt for the failed step of
    ``source_run_id``. ``status`` is ``PROPOSED`` / ``APPROVED`` / ``REJECTED`` /
    ``EXECUTED`` / ``FAILED`` (see ``autoprompt_runner.recovery``). When the recovery is
    executed a new run is created and its id is stored in ``recovery_run_id``; the original
    run's records are never mutated (only this linking metadata is recorded).
    """

    id: int
    source_run_id: int
    recovery_run_id: Optional[int]
    failed_step_id: Optional[int]
    status: str
    recovery_prompt: str
    reason: Optional[str]
    created_at: str
    decided_at: Optional[str] = None
    executed_at: Optional[str] = None


@dataclass
class WorkerHeartbeat:
    """A local worker's heartbeat row, persisted in ``worker_heartbeats``.

    The background worker records a heartbeat on start and refreshes ``updated_at`` each poll
    cycle; on a clean shutdown it sets ``status`` to ``STOPPED`` with ``stopped_at``. A
    crashed worker leaves a stale ``ACTIVE`` heartbeat (an old ``updated_at``), which lets the
    reconciler tell a live worker from a dead one. This is a single-machine signal -- there is
    no distributed worker coordination.
    """

    id: int
    worker_id: str
    status: str
    started_at: str
    updated_at: str
    stopped_at: Optional[str] = None


@dataclass
class RunCheckpoint:
    """A pre-execution workspace checkpoint, persisted in ``run_checkpoints``.

    Captures the read-only Git state of a run's workspace *before* the agent executes, so the
    user can later inspect or roll back to it. ``git_head_before`` / ``git_branch_before`` /
    ``git_status_before`` are the captured HEAD commit, branch, and porcelain status;
    ``checkpoint_ref`` is the ref a rollback would reset to (the captured HEAD -- no new commit
    or tag is created). ``status`` is ``CREATED`` / ``RESTORED`` / ``FAILED`` / ``SKIPPED``
    (see ``autoprompt_runner.checkpoints``). A non-Git or missing workspace yields a SKIPPED
    record and never fails the run. ``restore_error`` holds a skip reason (SKIPPED) or a
    rollback error (FAILED). The checkpoint is metadata only -- it stores no file contents and
    no secrets.
    """

    id: int
    run_id: int
    step_id: Optional[int]
    workspace_path: str
    git_head_before: Optional[str]
    git_branch_before: Optional[str]
    git_status_before: Optional[str]
    checkpoint_ref: Optional[str]
    status: str
    created_at: str
    restored_at: Optional[str] = None
    restore_error: Optional[str] = None


@dataclass
class RunCommit:
    """A local-commit record for a run's workspace, persisted in ``run_commits``.

    Tracks an explicit, user-confirmed local Git commit of a successful run's changes.
    ``status`` is ``PROPOSED`` / ``COMMITTED`` / ``FAILED`` / ``SKIPPED`` (see
    ``autoprompt_runner.commits``). ``changed_files`` is the newline-joined list of files
    considered/committed; ``commit_hash`` is set once a commit succeeds. This workflow is
    **local only** -- it never pushes, opens a PR, or runs a destructive Git command -- and it
    never stages secret-like files. ``error`` holds a skip/failure reason.
    """

    id: int
    run_id: int
    workspace_path: str
    status: str
    commit_hash: Optional[str]
    commit_message: Optional[str]
    changed_files: Optional[str]
    created_at: str
    committed_at: Optional[str] = None
    error: Optional[str] = None


@dataclass
class AppTarget:
    """A Claude Code (desktop app) injection target, persisted in ``app_targets``.

    Identifies a specific app *session/pane*, not just "the Claude Code app", so prompts are
    injected into the intended place. ``target_mode`` is ``active_window_manual`` /
    ``window_title_hint`` / ``future_accessibility`` (only ``active_window_manual`` is
    implemented). ``submit_mode`` is ``paste_only`` / ``paste_and_enter`` /
    ``paste_and_ctrl_enter``. ``confirm_before_inject`` is stored as 0/1 but exposed as a bool.
    ``status`` is ``ACTIVE`` / ``DISABLED``. This row carries no secrets.
    """

    id: int
    name: str
    app_name: str
    window_title_hint: Optional[str]
    session_label: Optional[str]
    project_path: Optional[str]
    worktree_path: Optional[str]
    pane_label: Optional[str]
    pane_index: Optional[int]
    target_mode: str
    submit_mode: str
    confirm_before_inject: bool
    status: str
    created_at: str
    updated_at: str
    last_used_at: Optional[str] = None


@dataclass
class PromptQueue:
    """A queue of prompts bound to an :class:`AppTarget`, persisted in ``prompt_queues``.

    Prompts are injected into the Claude Code app one at a time (the queue never runs the
    Claude Code CLI). ``status`` is ``DRAFT`` / ``READY`` / ``RUNNING`` / ``PAUSED`` / ``DONE``
    / ``FAILED`` / ``CANCELLED`` (see ``autoprompt_runner.prompt_queue``).
    """

    id: int
    name: str
    description: Optional[str]
    app_target_id: Optional[int]
    project_path: Optional[str]
    status: str
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    paused_at: Optional[str] = None


@dataclass
class QueuedPrompt:
    """One prompt in a :class:`PromptQueue`, persisted in ``queued_prompts``.

    ``position`` is the 1-based order within the queue. ``status`` is ``PENDING`` /
    ``READY_TO_INJECT`` / ``INJECTING`` / ``SUBMITTED`` / ``WAITING_COMPLETION`` / ``DONE`` /
    ``FAILED`` / ``SKIPPED`` / ``CANCELLED``. Only one prompt per queue may be
    ``WAITING_COMPLETION`` at a time. The lifecycle timestamps record when each transition
    happened; ``last_error`` holds the most recent injection error, if any.
    """

    id: int
    queue_id: int
    position: int
    status: str
    title: Optional[str]
    prompt: str
    injected_at: Optional[str] = None
    submitted_at: Optional[str] = None
    completed_at: Optional[str] = None
    skipped_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    last_error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


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
