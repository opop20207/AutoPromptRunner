"""ClaudeCodeRunner: execute the Claude Code CLI through a subprocess.

This is the first real external agent provider. It runs the Claude Code CLI in
non-interactive print mode inside a workspace directory and captures the result. The
runner only executes Claude Code and records its output -- it never mutates files
itself, reads no secrets, and prints nothing. Claude Code itself may modify files
inside the workspace; that is the user's intent and is documented in the README.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from os import path as _ospath
from typing import List, Optional

from .. import config, processes
from ..models import AgentResult
from .base import AgentRunner

# Exit codes used when the subprocess could not produce its own return code.
_EXIT_TIMEOUT = 124
_EXIT_NOT_FOUND = 127
_EXIT_LAUNCH_ERROR = 1
_EXIT_CANCELLED = 130


def _now_iso() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


class ClaudeCodeRunner(AgentRunner):
    """Run the Claude Code CLI via ``subprocess.run`` and capture the result.

    Parameters
    ----------
    command:
        The CLI executable to invoke (default ``"claude"``).
    timeout_seconds:
        Hard timeout for the subprocess (default 1800).
    workspace:
        Directory the CLI runs in. If provided it must exist and be a directory.
    """

    def __init__(
        self,
        command: str = "claude",
        timeout_seconds: int = 1800,
        workspace: Optional[str] = None,
        extra_args: Optional[List[str]] = None,
    ) -> None:
        self.command = command
        # Clamp to the safety hard limit so a runner can never exceed the max runtime.
        self.timeout_seconds = max(1, min(int(timeout_seconds), config.TIMEOUT_SECONDS_HARD_LIMIT))
        self.workspace = workspace
        # Extra args come from a provider profile's default_args (already split, no shell).
        self.extra_args = list(extra_args or [])
        if self.workspace is not None and not _ospath.isdir(self.workspace):
            raise ValueError(f"workspace does not exist or is not a directory: {self.workspace}")

    @property
    def name(self) -> str:
        return "claude-code"

    def _build_argv(self, prompt: str) -> List[str]:
        # Non-interactive print mode: claude [extra args] -p "<prompt>". No shell is used.
        return [self.command, *self.extra_args, "-p", prompt]

    def run(self, prompt: str, run_id: Optional[int] = None) -> AgentResult:
        started_at = _now_iso()
        argv = self._build_argv(prompt)
        # subprocess.Popen (not subprocess.run) so the process can be registered and
        # cancelled while it runs. No shell is used.
        try:
            process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.workspace,
                shell=False,
                text=True,
            )
        except FileNotFoundError:
            return AgentResult(
                stdout="",
                stderr=(
                    f"claude-code: command not found: {self.command!r}. "
                    "Is the Claude Code CLI installed and on PATH?"
                ),
                exit_code=_EXIT_NOT_FOUND,
                started_at=started_at,
                finished_at=_now_iso(),
            )
        except OSError as exc:
            return AgentResult(
                stdout="",
                stderr=f"claude-code: failed to execute {self.command!r}: {exc}",
                exit_code=_EXIT_LAUNCH_ERROR,
                started_at=started_at,
                finished_at=_now_iso(),
            )

        if run_id is not None:
            processes.register_process(run_id, process)
        try:
            try:
                stdout, stderr = process.communicate(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                message = f"claude-code: timed out after {self.timeout_seconds}s"
                combined = f"{_as_text(stderr)}\n{message}".strip() if stderr else message
                return AgentResult(
                    stdout=_as_text(stdout), stderr=combined, exit_code=_EXIT_TIMEOUT,
                    started_at=started_at, finished_at=_now_iso(),
                )
            if run_id is not None and processes.was_terminated(run_id):
                message = "claude-code: run cancelled"
                combined = f"{_as_text(stderr)}\n{message}".strip() if stderr else message
                return AgentResult(
                    stdout=_as_text(stdout), stderr=combined, exit_code=_EXIT_CANCELLED,
                    started_at=started_at, finished_at=_now_iso(),
                )
            return AgentResult(
                stdout=_as_text(stdout), stderr=_as_text(stderr), exit_code=process.returncode,
                started_at=started_at, finished_at=_now_iso(),
            )
        finally:
            if run_id is not None:
                processes.unregister_process(run_id)
                processes.clear_terminated(run_id)
