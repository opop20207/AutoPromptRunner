"""The prompt-loop orchestration service.

``RunService`` creates runs, executes one step at a time through a provider runner,
persists each step, generates the next prompt, and gates execution behind an approval
when required. It enforces ``max_loops`` and never loops without a bound, satisfying
the AGENTS.md "Prompt Loop Rules".

Around each step it records artifacts: the read-only Git state before/after the step
(status, diff, diff stat, changed files) when the workspace is a Git repository, plus
the runner's stdout/stderr. The Git signal (changed files + diff stat) is also fed into
the PromptGenerator so the next prompt reflects what actually changed. A non-Git or
missing workspace never fails the run; Git artifacts are skipped and a compact warning
artifact is recorded instead.

Providers are resolved through a factory map (name -> factory(workspace,
timeout_seconds) -> AgentRunner), so provider-specific construction stays isolated.

Loop policy:
* require_approval = True  -> a single step runs per call; the run then pauses at
  WAITING_APPROVAL with a PENDING approval (or ends DONE/FAILED).
* require_approval = False -> steps auto-run up to ``max_loops`` or until a failure.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional, Tuple

from .. import artifacts, cancel, config, events, locks, processes, safety, storage, templates, worktrees
from .. import providers as provider_mgmt
from ..artifacts import ArtifactPayload, ArtifactType
from ..models import AgentResult, PromptGenerationContext, StepExecutionReport
from ..projects import ResolvedRunSettings, resolve_run_settings
from ..runners import AgentRunner, ClaudeCodeRunner, CodexRunner, MockRunner
from ..settings import load_settings
from ..state import RunStatus
from .prompt_generator import PromptGenerator

# A provider factory builds a runner from the per-run workspace and timeout.
ProviderFactory = Callable[[Optional[str], Optional[int]], AgentRunner]

_DEFAULT_TIMEOUT_SECONDS = 1800
_GIT_SKIPPED_REASON = "workspace missing or not a git repository; git artifacts skipped"


def _mock_factory(workspace: Optional[str], timeout_seconds: Optional[int]) -> AgentRunner:
    return MockRunner()


def _claude_code_factory(workspace: Optional[str], timeout_seconds: Optional[int]) -> AgentRunner:
    return ClaudeCodeRunner(
        workspace=workspace,
        timeout_seconds=timeout_seconds if timeout_seconds is not None else _DEFAULT_TIMEOUT_SECONDS,
    )


def _codex_factory(workspace: Optional[str], timeout_seconds: Optional[int]) -> AgentRunner:
    return CodexRunner(
        workspace=workspace,
        timeout_seconds=timeout_seconds if timeout_seconds is not None else _DEFAULT_TIMEOUT_SECONDS,
    )


# Supported providers and how to construct each runner: mock, claude-code, and codex.
DEFAULT_PROVIDER_FACTORIES: Dict[str, ProviderFactory] = {
    "mock": _mock_factory,
    "claude-code": _claude_code_factory,
    "codex": _codex_factory,
}

# Providers that require a workspace directory to run.
WORKSPACE_REQUIRED_PROVIDERS = ("claude-code", "codex")

# Run statuses from which a failure recovery may be proposed (see autoprompt_runner.recovery).
RECOVERABLE_STATUSES = (RunStatus.FAILED.value,)


class RunInputError(Exception):
    """Raised when run inputs are invalid or a named project is missing.

    ``kind`` is ``"invalid"`` (bad input) or ``"not_found"`` (named project absent);
    callers map it to a CLI exit code or an HTTP status. No database is created when the
    prompt is empty.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def resolve_run_inputs(
    db_path: Optional[str],
    *,
    prompt: Optional[str] = None,
    project: Optional[str] = None,
    provider: Optional[str] = None,
    workspace: Optional[str] = None,
    max_loops: Optional[int] = None,
    timeout_seconds: Optional[int] = None,
    no_approval: bool = False,
    template: Optional[str] = None,
    goal: Optional[str] = None,
    extra_context: Optional[str] = None,
    worktree: Optional[str] = None,
    config_path: Optional[str] = None,
) -> Tuple[str, ResolvedRunSettings]:
    """Resolve and validate run inputs, shared by the CLI and the HTTP API.

    Applies project/default-project resolution (the same rules the CLI uses) and the
    same validation, raising :class:`RunInputError` on a bad input or a missing named
    project. The prompt comes from either ``prompt`` or a named ``template`` (rendered
    with the project/workspace/goal/extra_context values) -- supplying both is an error.
    Returns the cleaned prompt and the resolved settings.
    """
    prompt_text = (prompt or "").strip()
    template_name = (template or "").strip()
    if prompt_text and template_name:
        raise RunInputError("invalid", "provide either --prompt or --template, not both")
    if not prompt_text and not template_name:
        raise RunInputError("invalid", "--prompt or --template is required")
    app_settings = load_settings(config_path)
    db_path = storage.init_db(db_path)
    if project:
        selected = storage.get_project_by_name(db_path, project)
        if selected is None:
            raise RunInputError("not_found", f"project '{project}' not found")
    else:
        selected = storage.get_default_project(db_path)

    # Config/env run defaults sit below a project profile and above the built-in defaults:
    # they apply only when no project profile provides the value.
    eff_provider = provider
    eff_max_loops = max_loops
    eff_timeout = timeout_seconds
    if selected is None:
        if eff_provider is None:
            eff_provider = app_settings.defaults.provider
        if eff_max_loops is None:
            eff_max_loops = app_settings.defaults.max_loops
        if eff_timeout is None:
            eff_timeout = app_settings.defaults.timeout_seconds

    # Workspace precedence: explicit --workspace > --worktree path > project repo_path >
    # config default workspace. A named worktree is always validated.
    worktree_name = (worktree or "").strip()
    worktree_path: Optional[str] = None
    if worktree_name:
        wt = storage.get_worktree_by_name(db_path, worktree_name)
        if wt is None:
            raise RunInputError("not_found", f"worktree '{worktree_name}' not found")
        if wt.status == worktrees.WORKTREE_ARCHIVED:
            raise RunInputError("invalid", f"worktree '{worktree_name}' is archived")
        worktree_path = wt.path
    effective_workspace = workspace if workspace else worktree_path
    if not effective_workspace and selected is None and app_settings.defaults.workspace:
        effective_workspace = app_settings.defaults.workspace

    settings = resolve_run_settings(
        selected,
        provider=eff_provider,
        max_loops=eff_max_loops,
        timeout_seconds=eff_timeout,
        workspace=effective_workspace,
        no_approval=no_approval,
    )
    if template_name:
        tmpl = storage.get_template_by_name(db_path, template_name)
        if tmpl is None:
            raise RunInputError("not_found", f"template '{template_name}' not found")
        values = templates.build_render_values(
            project_name=selected.name if selected is not None else "",
            workspace=settings.workspace,
            goal=goal,
            extra_context=extra_context,
        )
        text = templates.render_template(tmpl.body, values).strip()
        if not text:
            raise RunInputError("invalid", f"template '{template_name}' rendered an empty prompt")
    else:
        text = prompt_text
    # Resolve the provider: a provider-profile name (any type) or a built-in type name.
    # A profile is rejected here when disabled or when its external command is unavailable
    # (mock is always available), so the CLI/API reject before any run is created.
    profile = storage.get_provider_profile_by_name(db_path, settings.provider)
    if profile is not None:
        try:
            provider_mgmt.ensure_provider_runnable(profile)
        except provider_mgmt.ProviderError as exc:
            raise RunInputError("invalid", str(exc)) from exc
        provider_type = profile.type
    elif settings.provider in DEFAULT_PROVIDER_FACTORIES:
        provider_type = settings.provider
    else:
        supported = ", ".join(sorted(DEFAULT_PROVIDER_FACTORIES))
        raise RunInputError(
            "invalid",
            f"unsupported provider '{settings.provider}'. Use a provider profile name or one of: {supported}",
        )
    if settings.max_loops < 1:
        raise RunInputError("invalid", "--max-loops must be >= 1")
    if settings.max_loops > config.MAX_LOOPS_HARD_LIMIT:
        raise RunInputError(
            "invalid", f"--max-loops must not exceed the hard limit of {config.MAX_LOOPS_HARD_LIMIT}"
        )
    if settings.timeout_seconds < 1:
        raise RunInputError("invalid", "--timeout-seconds must be >= 1")
    if settings.timeout_seconds > config.TIMEOUT_SECONDS_HARD_LIMIT:
        raise RunInputError(
            "invalid", f"--timeout-seconds must not exceed the hard limit of {config.TIMEOUT_SECONDS_HARD_LIMIT}"
        )
    if provider_type in WORKSPACE_REQUIRED_PROVIDERS:
        if not settings.workspace:
            raise RunInputError(
                "invalid",
                f"--workspace is required for the {provider_type} provider "
                "(pass --workspace or use a project repo_path)",
            )
        if not os.path.isdir(settings.workspace):
            raise RunInputError("invalid", f"workspace does not exist or is not a directory: {settings.workspace}")
    try:
        safety.validate_workspace_allowed(settings.workspace)
    except ValueError as exc:
        raise RunInputError("invalid", str(exc)) from exc
    return text, settings


def _extract_git_context(payloads: List[ArtifactPayload]) -> Tuple[List[str], str]:
    """Pull changed-files and diff-stat out of the captured Git artifact payloads."""
    by_type = {payload.type: payload.content for payload in payloads}
    changed = by_type.get(ArtifactType.CHANGED_FILES.value, "")
    changed_files = [line for line in changed.splitlines() if line.strip()]
    diff_stat = by_type.get(ArtifactType.GIT_DIFF_STAT.value, "")
    return changed_files, diff_stat


class RunServiceError(Exception):
    """Raised for run-control errors: missing run, no pending approval, terminal run.

    ``kind`` (``not_found`` / ``terminal`` / ``no_pending`` / ``error``) lets HTTP
    callers map to a precise status code; the CLI treats them uniformly.
    """

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


class SafetyBlockedError(RunServiceError):
    """Raised when a prompt contains a blocked (destructive) command pattern.

    The run is recorded as FAILED with a ``safety_blocker`` artifact before this is
    raised. ``kind`` is ``"blocked"``; the API maps it to HTTP 400.
    """

    def __init__(self, run_id: int, blocked: List[str]) -> None:
        super().__init__(
            "run blocked by safety: prompt contains blocked command pattern(s): " + ", ".join(blocked),
            kind="blocked",
        )
        self.run_id = run_id
        self.blocked = list(blocked)


class WorkspaceLockedError(RunServiceError):
    """Raised when a run cannot execute because another active run holds the workspace.

    ``kind`` is ``"locked"``; the CLI exits non-zero and the API maps it to HTTP 409.
    """

    def __init__(self, run_id: int, workspace: str, holder_run_id: int) -> None:
        super().__init__(
            f"workspace is locked by an active run (run {holder_run_id}): {workspace}",
            kind="locked",
        )
        self.run_id = run_id
        self.workspace = workspace
        self.holder_run_id = holder_run_id


class RunService:
    """Coordinates runners, storage, prompt generation, the approval gate, and artifacts."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        providers: Optional[Dict[str, ProviderFactory]] = None,
        generator: Optional[PromptGenerator] = None,
    ) -> None:
        self.db_path = storage.init_db(db_path)  # ensure DB exists; store resolved path
        # ``_custom_providers`` records whether a caller injected a factory map (tests/custom
        # construction). When custom, an injected factory wins for a provider profile so the
        # runner stays offline; otherwise a profile is built from its configured command.
        self._custom_providers = providers is not None
        self.providers = providers if providers is not None else dict(DEFAULT_PROVIDER_FACTORIES)
        self.generator = generator or PromptGenerator()

    # -- public API -----------------------------------------------------------

    def create_run_only(
        self,
        prompt: str,
        provider: str,
        max_loops: int,
        require_approval: bool = True,
        workspace: Optional[str] = None,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        project_id: Optional[int] = None,
    ) -> int:
        """Create a run row (status CREATED) without executing it. Returns the run id.

        Used for queued runs: the API/CLI create the run quickly and enqueue it, and a
        worker later calls :meth:`execute_queued_run`. Basic input limits are validated
        here; the prompt safety scan and workspace lock are applied at execution time.
        """
        if provider not in self.providers and storage.get_provider_profile_by_name(self.db_path, provider) is None:
            raise RunServiceError(f"unsupported provider: {provider}")
        safety.validate_max_loops(max_loops)  # hard-limit backstop (CLI/API validate earlier)
        safety.validate_timeout_seconds(timeout_seconds)
        run_id = storage.create_run(
            self.db_path,
            root_prompt=prompt,
            provider=provider,
            max_loops=max_loops,
            require_approval=require_approval,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
            project_id=project_id,
        )
        self._emit(run_id, events.RUN_CREATED, message="run created", payload={"provider": provider})
        return run_id

    def create_run_only_like(self, source_run, prompt: str) -> int:
        """Create a new CREATED run reusing another run's settings (for failure recovery).

        Reuses ``source_run``'s provider, workspace, timeout, max_loops, approval mode, and
        project so a recovery run executes under the same conditions; the run is not executed
        here (the caller links it, then enqueues or executes it). The original run's records
        are not touched.
        """
        timeout = source_run.timeout_seconds if source_run.timeout_seconds is not None else _DEFAULT_TIMEOUT_SECONDS
        return self.create_run_only(
            prompt=prompt,
            provider=source_run.provider,
            max_loops=source_run.max_loops,
            require_approval=source_run.require_approval,
            workspace=source_run.workspace,
            timeout_seconds=timeout,
            project_id=source_run.project_id,
        )

    def execute_run_step(self, run_id: int) -> StepExecutionReport:
        """Execute a created/queued run: safety scan, acquire the lock, drive the loop.

        Applies the same prompt safety scan (Prompt#14), workspace lock (Prompt#17), Git
        artifact capture (Prompt#7), and prompt-loop/approval policy as the synchronous
        path. The lock is released once the run is terminal or pauses at WAITING_APPROVAL.
        """
        run = storage.get_run(self.db_path, run_id)
        if run is None:
            raise RunServiceError(f"run {run_id} not found", kind="not_found")
        if run.status not in (RunStatus.CREATED.value, RunStatus.RUNNING.value):
            raise RunServiceError(f"run {run_id} is {run.status}; cannot execute", kind="terminal")

        # Backstop: reject a disabled or now-unavailable provider profile before executing
        # (the CLI/API also reject at creation time). Built-in names without a profile fall
        # through to the runner's own safe command-not-found handling.
        profile = storage.get_provider_profile_by_name(self.db_path, run.provider)
        if profile is not None:
            try:
                provider_mgmt.ensure_provider_runnable(profile)
            except provider_mgmt.ProviderError as exc:
                storage.update_run_status(self.db_path, run_id, RunStatus.FAILED.value)
                raise RunServiceError(str(exc), kind="invalid") from exc

        timeout_seconds = run.timeout_seconds if run.timeout_seconds is not None else _DEFAULT_TIMEOUT_SECONDS
        storage.update_run_status(self.db_path, run_id, RunStatus.RUNNING.value)
        self._emit(run_id, events.RUN_STARTED, message="run started", payload={"provider": run.provider})
        blocked = safety.scan_prompt_for_blocked_commands(run.root_prompt)
        if blocked:
            storage.create_artifact(
                self.db_path, run_id=run_id, artifact_type=safety.SAFETY_BLOCKER_ARTIFACT,
                content="blocked command pattern(s): " + ", ".join(blocked),
            )
            storage.update_run_status(self.db_path, run_id, RunStatus.FAILED.value)
            self._emit(run_id, events.SAFETY_WARNING, message="blocked command pattern(s): " + ", ".join(blocked))
            self._emit(run_id, events.RUN_FAILED, message="run blocked by safety", payload={"blocked": blocked})
            raise SafetyBlockedError(run_id, blocked)

        # Acquire the workspace lock before any runner execution (no-op without a workspace).
        try:
            locks.acquire_lock(self.db_path, run.workspace, run_id, timeout_seconds=timeout_seconds)
        except locks.LockConflictError as exc:
            storage.create_artifact(
                self.db_path, run_id=run_id, artifact_type=locks.LOCK_BLOCKER_ARTIFACT, content=str(exc),
            )
            storage.update_run_status(self.db_path, run_id, RunStatus.FAILED.value)
            self._emit(run_id, events.RUN_FAILED, message="run blocked: workspace locked")
            raise WorkspaceLockedError(run_id, exc.workspace_path, exc.holder_run_id) from exc
        if run.workspace:
            self._emit(run_id, events.LOCK_ACQUIRED, message=run.workspace)

        try:
            loop_index = len(storage.get_steps_for_run(self.db_path, run_id))
            return self._drive(
                run_id=run_id,
                root_prompt=run.root_prompt,
                provider=run.provider,
                max_loops=run.max_loops,
                require_approval=run.require_approval,
                loop_index=loop_index,
                prompt=run.root_prompt,
                workspace=run.workspace,
                timeout_seconds=timeout_seconds,
            )
        finally:
            # Release once execution pauses (WAITING_APPROVAL) or the run is terminal.
            self._release_workspace_lock(run_id, run.workspace)

    def execute_queued_run(self, run_id: int) -> StepExecutionReport:
        """Worker entry point: execute a previously created and queued run."""
        return self.execute_run_step(run_id)

    def create_and_execute_run(
        self,
        prompt: str,
        provider: str,
        max_loops: int,
        require_approval: bool = True,
        workspace: Optional[str] = None,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> StepExecutionReport:
        """Create a run and execute it immediately (the synchronous CLI/API path)."""
        run_id = self.create_run_only(
            prompt, provider, max_loops, require_approval=require_approval,
            workspace=workspace, timeout_seconds=timeout_seconds,
        )
        return self.execute_run_step(run_id)

    def start(
        self,
        prompt: str,
        provider: str,
        max_loops: int,
        require_approval: bool = True,
        workspace: Optional[str] = None,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> StepExecutionReport:
        """Backward-compatible alias for :meth:`create_and_execute_run`."""
        return self.create_and_execute_run(
            prompt, provider, max_loops, require_approval=require_approval,
            workspace=workspace, timeout_seconds=timeout_seconds,
        )

    def approve_and_continue(self, run_id: int) -> StepExecutionReport:
        """Approve the pending approval and execute the approved next prompt."""
        run = storage.get_run(self.db_path, run_id)
        if run is None:
            raise RunServiceError(f"run {run_id} not found", kind="not_found")
        if run.status in _TERMINAL_VALUES:
            raise RunServiceError(f"run {run_id} is {run.status}; cannot approve", kind="terminal")
        pending = storage.get_pending_approval(self.db_path, run_id)
        if pending is None:
            raise RunServiceError(f"no pending approval for run {run_id}", kind="no_pending")

        timeout_seconds = run.timeout_seconds if run.timeout_seconds is not None else _DEFAULT_TIMEOUT_SECONDS
        # Re-acquire the workspace lock before executing the next step. On a lock conflict
        # the approval is left PENDING so the user can retry once the workspace frees up.
        self._acquire_workspace_lock(run_id, run.workspace, timeout_seconds)
        try:
            storage.approve_pending_approval(self.db_path, run_id)
            next_index = len(storage.get_steps_for_run(self.db_path, run_id))
            storage.update_run_status(self.db_path, run_id, RunStatus.RUNNING.value)
            self._emit(run_id, events.RUN_STARTED, message="approved; continuing")
            return self._drive(
                run_id=run_id,
                root_prompt=run.root_prompt,
                provider=run.provider,
                max_loops=run.max_loops,
                require_approval=run.require_approval,
                loop_index=next_index,
                prompt=pending.next_prompt,
                workspace=run.workspace,
                timeout_seconds=timeout_seconds,
            )
        finally:
            self._release_workspace_lock(run_id, run.workspace)

    def reject(self, run_id: int) -> StepExecutionReport:
        """Reject the pending approval and stop the run."""
        run = storage.get_run(self.db_path, run_id)
        if run is None:
            raise RunServiceError(f"run {run_id} not found", kind="not_found")
        pending = storage.get_pending_approval(self.db_path, run_id)
        if pending is None:
            raise RunServiceError(f"no pending approval for run {run_id}", kind="no_pending")

        storage.reject_pending_approval(self.db_path, run_id)
        storage.update_run_status(self.db_path, run_id, RunStatus.STOPPED.value)
        self._release_workspace_lock(run_id, run.workspace)  # release if still held
        self._emit(run_id, events.RUN_STOPPED, message="rejected", payload={"status": "STOPPED"})
        steps = storage.get_steps_for_run(self.db_path, run_id)
        return StepExecutionReport(
            run_id=run_id,
            run_status=RunStatus.STOPPED.value,
            loop_index=steps[-1].loop_index if steps else 0,
            provider=run.provider,
            step_id=steps[-1].id if steps else None,
            message="rejected",
        )

    def cancel_run(self, run_id: int, reason: Optional[str] = None) -> cancel.CancelResult:
        """Cancel a queued, running, or waiting run and stop it.

        Queued -> cancel the queue job; waiting -> reject the pending approval; running ->
        best-effort terminate of a locally-registered agent process. In every non-terminal
        case the run is moved to STOPPED, its workspace lock is released, and a
        ``cancellation`` artifact is recorded. A terminal run is a clean error.
        """
        run = storage.get_run(self.db_path, run_id)
        if run is None:
            raise RunServiceError(f"run {run_id} not found", kind="not_found")
        if run.status in _TERMINAL_VALUES:
            raise RunServiceError(f"run {run_id} is already {run.status}; cannot cancel", kind="terminal")

        cancellation_id = storage.request_run_cancellation(self.db_path, run_id, reason)
        self._store_cancellation_artifact(run_id, reason)
        self._emit(run_id, events.CANCELLATION_REQUESTED, message=reason or "cancellation requested")
        job = storage.get_job_by_run_id(self.db_path, run_id)
        terminated = False
        try:
            if run.status == RunStatus.RUNNING.value:
                # Best-effort: only reaches a process registered in *this* process.
                terminated = processes.terminate_process(run_id)
            if job is not None and job.status == storage.QUEUE_QUEUED:
                storage.cancel_job(self.db_path, run_id)
            if run.status == RunStatus.WAITING_APPROVAL.value:
                if storage.get_pending_approval(self.db_path, run_id) is not None:
                    storage.reject_pending_approval(self.db_path, run_id)
            storage.update_run_status(self.db_path, run_id, RunStatus.STOPPED.value)
            self._release_workspace_lock(run_id, run.workspace)
            storage.complete_run_cancellation(self.db_path, cancellation_id)
            self._emit(run_id, events.RUN_STOPPED, message="run cancelled", payload={"status": "STOPPED"})
        except Exception as exc:  # noqa: BLE001  (record the failure, then surface it cleanly)
            storage.fail_run_cancellation(self.db_path, cancellation_id, str(exc))
            self._store_cancellation_error_artifact(run_id, str(exc))
            raise RunServiceError(f"cancellation failed for run {run_id}: {exc}") from exc
        return cancel.CancelResult(
            run_id=run_id,
            run_status=RunStatus.STOPPED.value,
            cancelled=True,
            terminated=terminated,
            reason=reason,
            message="run cancelled and stopped",
        )

    def _store_cancellation_artifact(self, run_id: int, reason: Optional[str]) -> None:
        storage.create_artifact(
            self.db_path, run_id=run_id, artifact_type=cancel.CANCELLATION_ARTIFACT,
            content=reason or "run cancellation requested",
        )

    def _store_cancellation_error_artifact(self, run_id: int, error: str) -> None:
        storage.create_artifact(
            self.db_path, run_id=run_id, artifact_type=cancel.CANCELLATION_ERROR_ARTIFACT, content=error,
        )

    # -- internals ------------------------------------------------------------

    def _make_runner(self, provider: str, workspace: Optional[str], timeout_seconds: Optional[int]) -> AgentRunner:
        """Build the runner for ``provider`` (a profile name or a built-in type name).

        A provider profile resolves to its configured runner type and command; the timeout
        falls back to the profile default when not given explicitly. When a custom factory
        map was injected (tests), an injected factory for the profile name or type wins so
        the runner stays offline. Names without a profile use the built-in factory map.
        """
        profile = storage.get_provider_profile_by_name(self.db_path, provider)
        if profile is not None:
            effective_timeout = timeout_seconds if timeout_seconds is not None else profile.default_timeout_seconds
            if self._custom_providers:
                injected = self.providers.get(provider) or self.providers.get(profile.type)
                if injected is not None:
                    return injected(workspace, effective_timeout)
            return provider_mgmt.build_runner_for_profile(profile, workspace, effective_timeout)
        factory = self.providers.get(provider)
        if factory is None:
            raise RunServiceError(f"unsupported provider: {provider}")
        return factory(workspace, timeout_seconds)

    def _acquire_workspace_lock(self, run_id: int, workspace: Optional[str], timeout_seconds: Optional[int]):
        """Acquire the workspace lock, converting a conflict to WorkspaceLockedError."""
        if not workspace:
            return None
        try:
            lock = locks.acquire_lock(self.db_path, workspace, run_id, timeout_seconds=timeout_seconds)
        except locks.LockConflictError as exc:
            raise WorkspaceLockedError(run_id, exc.workspace_path, exc.holder_run_id) from exc
        self._emit(run_id, events.LOCK_ACQUIRED, message=workspace)
        return lock

    def _run_was_cancelled(self, run_id: int) -> bool:
        """True if the run has been moved to STOPPED (e.g. by ``cancel_run``)."""
        run = storage.get_run(self.db_path, run_id)
        return run is not None and run.status == RunStatus.STOPPED.value

    def _cancelled_report(self, run_id: int, loop_index: int, provider: str) -> StepExecutionReport:
        steps = storage.get_steps_for_run(self.db_path, run_id)
        return StepExecutionReport(
            run_id=run_id, run_status=RunStatus.STOPPED.value, loop_index=loop_index,
            provider=provider, step_id=steps[-1].id if steps else None, message="cancelled",
        )

    def _release_workspace_lock(self, run_id: int, workspace: Optional[str]) -> None:
        """Release this run's workspace lock if one is held (no-op without a workspace)."""
        if not workspace:
            return
        locks.release_lock(self.db_path, run_id)
        self._emit(run_id, events.LOCK_RELEASED, message=workspace)

    def _emit(self, run_id, type, message=None, payload=None, step_id=None) -> None:
        """Emit a run event for live log streaming; never let event emission break a run."""
        try:
            events.create_event(self.db_path, run_id, type, message=message, payload=payload, step_id=step_id)
        except Exception:  # noqa: BLE001 - events are best-effort, never fatal
            pass

    def _record_step(
        self, run_id, loop_index, prompt, result, status, next_prompt, git_payloads, safety_warnings, streamed,
    ) -> int:
        """Persist a step + its artifacts/warnings and emit the matching live log events."""
        step_id = self._persist_step(run_id, loop_index, prompt, result, status, next_prompt)
        self._store_artifacts(run_id, step_id, git_payloads, result)
        self._store_warnings(run_id, step_id, safety_warnings)
        # Emit captured output for any stream the runner did not already stream live.
        if not streamed.get("stdout") and (result.stdout or "").strip():
            self._emit(run_id, events.STDOUT, message=result.stdout, step_id=step_id)
        if not streamed.get("stderr") and (result.stderr or "").strip():
            self._emit(run_id, events.STDERR, message=result.stderr, step_id=step_id)
        for warning in safety_warnings:
            self._emit(run_id, events.SAFETY_WARNING, message=warning, step_id=step_id)
        self._emit(
            run_id, events.STEP_FINISHED, message=f"step {loop_index} {status}",
            payload={"loop_index": loop_index, "status": status, "exit_code": result.exit_code}, step_id=step_id,
        )
        return step_id

    def _drive(
        self,
        run_id: int,
        root_prompt: str,
        provider: str,
        max_loops: int,
        require_approval: bool,
        loop_index: int,
        prompt: str,
        workspace: Optional[str],
        timeout_seconds: Optional[int],
    ) -> StepExecutionReport:
        """Execute steps from ``loop_index`` per the loop policy and return a report.

        The loop is bounded: it runs at most until ``max_loops`` is reached, stops on a
        failed step, and -- when approval is required -- returns after a single step.
        Each executed step records Git (when applicable) and runner artifacts, and feeds
        the Git signal into the next-prompt generator.
        """
        while True:
            # Stop cleanly if the run was cancelled (STOPPED) before this iteration.
            if self._run_was_cancelled(run_id):
                return self._cancelled_report(run_id, loop_index, provider)
            git_capture = artifacts.workspace_is_git(workspace)
            status_before = artifacts.capture_git_status(workspace) if git_capture else ""
            runner = self._make_runner(provider, workspace, timeout_seconds)
            # Live log streaming: emit each captured output line as an event. Streams that the
            # runner reports here are not re-emitted in full below (avoids duplicate content).
            streamed = {"stdout": False, "stderr": False}

            def _on_output(stream, line, _streamed=streamed):
                _streamed[stream] = True
                self._emit(run_id, stream, message=line)

            runner.set_output_callback(_on_output)
            self._emit(
                run_id, events.STEP_STARTED, message=f"step {loop_index} started",
                payload={"loop_index": loop_index},
            )
            result = runner.run(prompt, run_id=run_id)
            loops_done = loop_index + 1

            # Capture Git artifacts once; reuse them for both storage and the generator.
            git_payloads = self._git_payloads(workspace, git_capture, status_before)
            changed_files, diff_stat = _extract_git_context(git_payloads)
            context = PromptGenerationContext(
                root_prompt=root_prompt,
                previous_prompt=prompt,
                exit_code=result.exit_code,
                loop_index=loop_index,
                max_loops=max_loops,
                stdout=result.stdout,
                stderr=result.stderr,
                changed_files=changed_files,
                git_diff_stat=diff_stat,
                provider=provider,
                workspace=workspace,
                require_approval=require_approval,
            )

            # Safety from the captured change set (names / diff stats only, no contents).
            safety_warnings = safety.build_safety_warnings(changed_files=changed_files, diff_stat=diff_stat)
            risky = safety.detect_risky_run(prompt, changed_files, diff_stat) is not None

            # Honor a cancellation that landed while this step executed: record the step +
            # artifacts but do not overwrite the externally-set STOPPED status.
            if self._run_was_cancelled(run_id):
                step_id = self._record_step(
                    run_id, loop_index, prompt, result, RunStatus.STOPPED.value, None,
                    git_payloads, safety_warnings, streamed,
                )
                self._emit(run_id, events.RUN_STOPPED, message="run stopped", payload={"status": "STOPPED"})
                return StepExecutionReport(
                    run_id=run_id, run_status=RunStatus.STOPPED.value, loop_index=loop_index,
                    provider=provider, step_id=step_id, exit_code=result.exit_code, message="cancelled",
                )

            if result.exit_code != 0:
                nxt = self.generator.generate(context)
                step_id = self._record_step(
                    run_id, loop_index, prompt, result, RunStatus.FAILED.value, nxt.prompt,
                    git_payloads, safety_warnings, streamed,
                )
                storage.update_run_status(
                    self.db_path, run_id, RunStatus.FAILED.value, finished_at=result.finished_at
                )
                self._emit(run_id, events.RUN_FAILED, message="run failed", payload={"exit_code": result.exit_code})
                return StepExecutionReport(
                    run_id=run_id, run_status=RunStatus.FAILED.value, loop_index=loop_index,
                    provider=provider, step_id=step_id, exit_code=result.exit_code,
                    next_prompt=nxt.prompt, message="step failed",
                )

            if loops_done >= max_loops:
                step_id = self._record_step(
                    run_id, loop_index, prompt, result, RunStatus.DONE.value, None,
                    git_payloads, safety_warnings, streamed,
                )
                storage.update_run_status(
                    self.db_path, run_id, RunStatus.DONE.value, finished_at=result.finished_at
                )
                self._emit(run_id, events.RUN_DONE, message="max_loops reached", payload={"status": "DONE"})
                return StepExecutionReport(
                    run_id=run_id, run_status=RunStatus.DONE.value, loop_index=loop_index,
                    provider=provider, step_id=step_id, exit_code=0, message="max_loops reached",
                )

            nxt = self.generator.generate(context)
            step_id = self._record_step(
                run_id, loop_index, prompt, result, RunStatus.DONE.value, nxt.prompt,
                git_payloads, safety_warnings, streamed,
            )

            # A risky change (secret-like file or large diff) forces an approval gate even
            # when require_approval is False.
            if require_approval or risky:
                approval_id = storage.create_approval(self.db_path, run_id, step_id, nxt.prompt)
                storage.update_run_status(self.db_path, run_id, RunStatus.WAITING_APPROVAL.value)
                message = "waiting for approval (risky change)" if risky and not require_approval else "waiting for approval"
                self._emit(
                    run_id, events.APPROVAL_PENDING, message=message,
                    payload={"approval_id": approval_id}, step_id=step_id,
                )
                return StepExecutionReport(
                    run_id=run_id, run_status=RunStatus.WAITING_APPROVAL.value, loop_index=loop_index,
                    provider=provider, step_id=step_id, exit_code=0, next_prompt=nxt.prompt,
                    approval_id=approval_id, message=message,
                )

            # Auto-run: advance to the next step with the generated prompt.
            loop_index += 1
            prompt = nxt.prompt

    def _git_payloads(self, workspace: Optional[str], git_capture: bool, status_before: str) -> List[ArtifactPayload]:
        """Capture the Git artifact payloads for a step (or a single skip warning)."""
        if git_capture and workspace is not None:
            status_after = artifacts.capture_git_status(workspace)
            return artifacts.collect_post_step_git_artifacts(workspace, status_before or "", status_after)
        return [artifacts.git_skipped_artifact(_GIT_SKIPPED_REASON)]

    def _store_artifacts(self, run_id: int, step_id: int, git_payloads: List[ArtifactPayload], result: AgentResult) -> None:
        """Persist the Git payloads plus the runner stdout/stderr artifacts for a step."""
        payloads = list(git_payloads) + artifacts.runner_output_artifacts(result.stdout, result.stderr)
        for payload in payloads:
            storage.create_artifact(
                self.db_path,
                run_id=run_id,
                artifact_type=payload.type,
                content=payload.content,
                step_id=step_id,
            )

    def _store_warnings(self, run_id: int, step_id: int, warnings: List[str]) -> None:
        """Persist any non-fatal safety warnings as artifacts for a step."""
        for warning in warnings:
            storage.create_artifact(
                self.db_path,
                run_id=run_id,
                artifact_type=safety.SAFETY_WARNING_ARTIFACT,
                content=warning,
                step_id=step_id,
            )

    def _persist_step(
        self,
        run_id: int,
        loop_index: int,
        prompt: str,
        result: AgentResult,
        status: str,
        next_prompt: Optional[str],
    ) -> int:
        return storage.create_step(
            self.db_path,
            run_id=run_id,
            loop_index=loop_index,
            prompt=prompt,
            status=status,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            started_at=result.started_at,
            finished_at=result.finished_at,
            next_prompt=next_prompt,
        )


_TERMINAL_VALUES = {RunStatus.DONE.value, RunStatus.FAILED.value, RunStatus.STOPPED.value}
