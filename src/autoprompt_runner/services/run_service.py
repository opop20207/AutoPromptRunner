"""The prompt-loop orchestration service.

``RunService`` creates runs, executes one step at a time through a provider runner,
persists each step, generates the next prompt, and gates execution behind an approval
when required. It enforces ``max_loops`` and never loops without a bound, satisfying
the AGENTS.md "Prompt Loop Rules".

Providers are resolved through a factory map (name -> factory(workspace,
timeout_seconds) -> AgentRunner), so provider-specific construction stays isolated and
no subprocess logic leaks into the service.

Loop policy:
* require_approval = True  -> a single step runs per call; the run then pauses at
  WAITING_APPROVAL with a PENDING approval (or ends DONE/FAILED).
* require_approval = False -> steps auto-run up to ``max_loops`` or until a failure.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from .. import storage
from ..models import AgentResult, StepExecutionReport
from ..runners import AgentRunner, ClaudeCodeRunner, MockRunner
from ..state import RunStatus
from .prompt_generator import PromptGenerator

# A provider factory builds a runner from the per-run workspace and timeout.
ProviderFactory = Callable[[Optional[str], Optional[int]], AgentRunner]

_DEFAULT_TIMEOUT_SECONDS = 1800


def _mock_factory(workspace: Optional[str], timeout_seconds: Optional[int]) -> AgentRunner:
    return MockRunner()


def _claude_code_factory(workspace: Optional[str], timeout_seconds: Optional[int]) -> AgentRunner:
    return ClaudeCodeRunner(
        workspace=workspace,
        timeout_seconds=timeout_seconds if timeout_seconds is not None else _DEFAULT_TIMEOUT_SECONDS,
    )


# Supported providers and how to construct each runner. Only mock and claude-code.
DEFAULT_PROVIDER_FACTORIES: Dict[str, ProviderFactory] = {
    "mock": _mock_factory,
    "claude-code": _claude_code_factory,
}


class RunServiceError(Exception):
    """Raised for run-control errors: missing run, no pending approval, terminal run."""


class RunService:
    """Coordinates runners, storage, prompt generation, and the approval gate."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        providers: Optional[Dict[str, ProviderFactory]] = None,
        generator: Optional[PromptGenerator] = None,
    ) -> None:
        self.db_path = storage.init_db(db_path)  # ensure DB exists; store resolved path
        self.providers = providers if providers is not None else dict(DEFAULT_PROVIDER_FACTORIES)
        self.generator = generator or PromptGenerator()

    # -- public API -----------------------------------------------------------

    def start(
        self,
        prompt: str,
        provider: str,
        max_loops: int,
        require_approval: bool = True,
        workspace: Optional[str] = None,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> StepExecutionReport:
        """Create a run and execute it according to the loop policy."""
        if provider not in self.providers:
            raise RunServiceError(f"unsupported provider: {provider}")
        run_id = storage.create_run(
            self.db_path,
            root_prompt=prompt,
            provider=provider,
            max_loops=max_loops,
            require_approval=require_approval,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
        )
        storage.update_run_status(self.db_path, run_id, RunStatus.RUNNING.value)
        return self._drive(
            run_id=run_id,
            root_prompt=prompt,
            provider=provider,
            max_loops=max_loops,
            require_approval=require_approval,
            loop_index=0,
            prompt=prompt,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
        )

    def approve_and_continue(self, run_id: int) -> StepExecutionReport:
        """Approve the pending approval and execute the approved next prompt."""
        run = storage.get_run(self.db_path, run_id)
        if run is None:
            raise RunServiceError(f"run {run_id} not found")
        if run.status in _TERMINAL_VALUES:
            raise RunServiceError(f"run {run_id} is {run.status}; cannot approve")
        pending = storage.get_pending_approval(self.db_path, run_id)
        if pending is None:
            raise RunServiceError(f"no pending approval for run {run_id}")

        storage.approve_pending_approval(self.db_path, run_id)
        next_index = len(storage.get_steps_for_run(self.db_path, run_id))
        storage.update_run_status(self.db_path, run_id, RunStatus.RUNNING.value)
        return self._drive(
            run_id=run_id,
            root_prompt=run.root_prompt,
            provider=run.provider,
            max_loops=run.max_loops,
            require_approval=run.require_approval,
            loop_index=next_index,
            prompt=pending.next_prompt,
            workspace=run.workspace,
            timeout_seconds=run.timeout_seconds if run.timeout_seconds is not None else _DEFAULT_TIMEOUT_SECONDS,
        )

    def reject(self, run_id: int) -> StepExecutionReport:
        """Reject the pending approval and stop the run."""
        run = storage.get_run(self.db_path, run_id)
        if run is None:
            raise RunServiceError(f"run {run_id} not found")
        pending = storage.get_pending_approval(self.db_path, run_id)
        if pending is None:
            raise RunServiceError(f"no pending approval for run {run_id}")

        storage.reject_pending_approval(self.db_path, run_id)
        storage.update_run_status(self.db_path, run_id, RunStatus.STOPPED.value)
        steps = storage.get_steps_for_run(self.db_path, run_id)
        return StepExecutionReport(
            run_id=run_id,
            run_status=RunStatus.STOPPED.value,
            loop_index=steps[-1].loop_index if steps else 0,
            provider=run.provider,
            step_id=steps[-1].id if steps else None,
            message="rejected",
        )

    # -- internals ------------------------------------------------------------

    def _make_runner(self, provider: str, workspace: Optional[str], timeout_seconds: Optional[int]) -> AgentRunner:
        factory = self.providers.get(provider)
        if factory is None:
            raise RunServiceError(f"unsupported provider: {provider}")
        return factory(workspace, timeout_seconds)

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
        """
        while True:
            runner = self._make_runner(provider, workspace, timeout_seconds)
            result = runner.run(prompt)
            loops_done = loop_index + 1

            if result.exit_code != 0:
                nxt = self.generator.generate(
                    root_prompt, prompt, result.stdout, result.stderr, result.exit_code, loop_index
                )
                step_id = self._persist_step(run_id, loop_index, prompt, result, RunStatus.FAILED.value, nxt.prompt)
                storage.update_run_status(
                    self.db_path, run_id, RunStatus.FAILED.value, finished_at=result.finished_at
                )
                return StepExecutionReport(
                    run_id=run_id, run_status=RunStatus.FAILED.value, loop_index=loop_index,
                    provider=provider, step_id=step_id, exit_code=result.exit_code,
                    next_prompt=nxt.prompt, message="step failed",
                )

            if loops_done >= max_loops:
                step_id = self._persist_step(run_id, loop_index, prompt, result, RunStatus.DONE.value, None)
                storage.update_run_status(
                    self.db_path, run_id, RunStatus.DONE.value, finished_at=result.finished_at
                )
                return StepExecutionReport(
                    run_id=run_id, run_status=RunStatus.DONE.value, loop_index=loop_index,
                    provider=provider, step_id=step_id, exit_code=0, message="max_loops reached",
                )

            nxt = self.generator.generate(
                root_prompt, prompt, result.stdout, result.stderr, result.exit_code, loop_index
            )
            step_id = self._persist_step(run_id, loop_index, prompt, result, RunStatus.DONE.value, nxt.prompt)

            if require_approval:
                approval_id = storage.create_approval(self.db_path, run_id, step_id, nxt.prompt)
                storage.update_run_status(self.db_path, run_id, RunStatus.WAITING_APPROVAL.value)
                return StepExecutionReport(
                    run_id=run_id, run_status=RunStatus.WAITING_APPROVAL.value, loop_index=loop_index,
                    provider=provider, step_id=step_id, exit_code=0, next_prompt=nxt.prompt,
                    approval_id=approval_id, message="waiting for approval",
                )

            # Auto-run: advance to the next step with the generated prompt.
            loop_index += 1
            prompt = nxt.prompt

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
