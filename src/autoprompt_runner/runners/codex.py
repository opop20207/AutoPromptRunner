"""CodexRunner: execute the Codex CLI through a subprocess.

A second real external agent provider, built on the same adapter model as
``ClaudeCodeRunner``. It runs the Codex CLI in non-interactive execution mode inside a
workspace directory and captures the result. The runner only executes Codex and records
its output -- it never mutates files itself, reads no secrets, and prints nothing. All
Codex-specific CLI details live here, isolated from the rest of the system. Codex itself
may modify files inside the workspace; that is the user's intent and is documented in
the README.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from os import path as _ospath
from typing import List, Optional

from ..models import AgentResult
from .base import AgentRunner

# Exit codes used when the subprocess could not produce its own return code.
_EXIT_TIMEOUT = 124
_EXIT_NOT_FOUND = 127
_EXIT_LAUNCH_ERROR = 1


def _now_iso() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


class CodexRunner(AgentRunner):
    """Run the Codex CLI via ``subprocess.run`` and capture the result.

    Parameters
    ----------
    command:
        The CLI executable to invoke (default ``"codex"``).
    timeout_seconds:
        Hard timeout for the subprocess (default 1800).
    workspace:
        Directory the CLI runs in. If provided it must exist and be a directory.
    """

    def __init__(
        self,
        command: str = "codex",
        timeout_seconds: int = 1800,
        workspace: Optional[str] = None,
    ) -> None:
        self.command = command
        self.timeout_seconds = int(timeout_seconds)
        self.workspace = workspace
        if self.workspace is not None and not _ospath.isdir(self.workspace):
            raise ValueError(f"workspace does not exist or is not a directory: {self.workspace}")

    @property
    def name(self) -> str:
        return "codex"

    def _build_argv(self, prompt: str) -> List[str]:
        # Non-interactive execution mode: ``codex exec "<prompt>"``. No shell is used,
        # and the prompt is passed as a single argument (never interpolated into a
        # shell string), so no quoting/injection concerns arise.
        return [self.command, "exec", prompt]

    def run(self, prompt: str) -> AgentResult:
        started_at = _now_iso()
        argv = self._build_argv(prompt)
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=self.workspace,
                shell=False,
                check=False,
            )
            return AgentResult(
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                exit_code=completed.returncode,
                started_at=started_at,
                finished_at=_now_iso(),
            )
        except FileNotFoundError:
            return AgentResult(
                stdout="",
                stderr=(
                    f"codex: command not found: {self.command!r}. "
                    "Is the Codex CLI installed and on PATH?"
                ),
                exit_code=_EXIT_NOT_FOUND,
                started_at=started_at,
                finished_at=_now_iso(),
            )
        except subprocess.TimeoutExpired as exc:
            partial = _as_text(exc.stderr)
            message = f"codex: timed out after {self.timeout_seconds}s"
            stderr = f"{partial}\n{message}".strip() if partial else message
            return AgentResult(
                stdout=_as_text(exc.stdout),
                stderr=stderr,
                exit_code=_EXIT_TIMEOUT,
                started_at=started_at,
                finished_at=_now_iso(),
            )
        except OSError as exc:
            return AgentResult(
                stdout="",
                stderr=f"codex: failed to execute {self.command!r}: {exc}",
                exit_code=_EXIT_LAUNCH_ERROR,
                started_at=started_at,
                finished_at=_now_iso(),
            )
