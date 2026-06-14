"""The common runner interface shared by every provider.

Keeping provider-specific logic behind this single abstraction is a core project rule
(see AGENTS.md, "Provider Adapter Rules"): adding a new provider means adding one
adapter that satisfies this interface, without changing the calling code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional

from ..models import AgentResult

# An optional output callback: ``callback(stream, line)`` where ``stream`` is "stdout" or
# "stderr". Used by the run service to turn captured output into live log events.
OutputCallback = Callable[[str, str], None]


class AgentRunner(ABC):
    """Abstract base class for all agent runners.

    Provider-specific logic (command construction, argument formatting, output
    parsing) must live inside a concrete subclass. Callers depend only on this
    interface, which keeps providers isolated and interchangeable.

    Concrete runners are configured from a **provider profile** (see
    ``autoprompt_runner.providers``): the subprocess-based runners accept ``command``,
    ``timeout_seconds``, ``workspace``, and ``extra_args`` constructor arguments so the
    executable, timeout, and default arguments come from the profile rather than being
    hardcoded. ``MockRunner`` accepts the same arguments and ignores them. Construction is
    centralized in ``providers.build_runner_for_profile``; this interface itself stays
    minimal (``name`` plus ``run``).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short, stable identifier for the provider (for example ``"mock"``)."""
        raise NotImplementedError

    def set_output_callback(self, callback: Optional["OutputCallback"]) -> None:
        """Register an optional ``callback(stream, line)`` for live stdout/stderr lines.

        Default is a no-op, so a runner that does not stream (the mock, or a test stub) is
        unaffected and ``run``'s signature is unchanged. The subprocess runners override this
        to emit each captured output line for live log events; the full output is still
        returned in the :class:`AgentResult`.
        """
        # No-op by default.
        return None

    @abstractmethod
    def run(self, prompt: str, run_id: Optional[int] = None) -> AgentResult:
        """Execute ``prompt`` and return a captured :class:`AgentResult`.

        Implementations must always populate ``stdout``, ``stderr``, ``exit_code``,
        ``started_at``, and ``finished_at`` -- including on failure. A non-zero exit
        code is a result to record and report, not an error to raise. ``run_id``, when
        given, lets a subprocess-based runner register its process so the run can be
        cancelled (see ``autoprompt_runner.processes``).
        """
        raise NotImplementedError
