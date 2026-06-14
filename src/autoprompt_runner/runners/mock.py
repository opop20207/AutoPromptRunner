"""A deterministic, offline runner used for tests and dry runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from ..models import AgentResult
from .base import AgentRunner


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class MockRunner(AgentRunner):
    """Runner that fabricates a result without calling any external tool.

    The stdout is deterministic for a given prompt, which makes it suitable for tests
    of the loop, reporting, and (later) the state machine. It never spawns a
    subprocess and never touches the network, so it satisfies the project rule that
    tests must not depend on a real CLI installation.

    It accepts the same optional constructor arguments as the real runners (``command``,
    ``timeout_seconds``, ``workspace``, ``extra_args``) so a provider profile of type
    ``mock`` can be built through the same path, but it ignores them -- nothing is executed.
    """

    def __init__(
        self,
        command: str = "mock",
        timeout_seconds: int = 30,
        workspace: Optional[str] = None,
        extra_args: Optional[List[str]] = None,
    ) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.workspace = workspace
        self.extra_args = list(extra_args or [])

    @property
    def name(self) -> str:
        return "mock"

    def run(self, prompt: str, run_id: Optional[int] = None) -> AgentResult:
        started_at = _now_iso()
        stdout = (
            "[mock] AutoPromptRunner MockRunner\n"
            f"[mock] received prompt: {prompt}\n"
            "[mock] no external tools were called; this is a canned result."
        )
        finished_at = _now_iso()
        return AgentResult(
            stdout=stdout,
            stderr="",
            exit_code=0,
            started_at=started_at,
            finished_at=finished_at,
        )
