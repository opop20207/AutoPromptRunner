"""A deterministic, offline runner used for tests and dry runs."""

from __future__ import annotations

from datetime import datetime, timezone

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
    """

    @property
    def name(self) -> str:
        return "mock"

    def run(self, prompt: str) -> AgentResult:
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
