"""The common runner interface shared by every provider.

Keeping provider-specific logic behind this single abstraction is a core project rule
(see AGENTS.md, "Provider Adapter Rules"): adding a new provider means adding one
adapter that satisfies this interface, without changing the calling code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import AgentResult


class AgentRunner(ABC):
    """Abstract base class for all agent runners.

    Provider-specific logic (command construction, argument formatting, output
    parsing) must live inside a concrete subclass. Callers depend only on this
    interface, which keeps providers isolated and interchangeable.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short, stable identifier for the provider (for example ``"mock"``)."""
        raise NotImplementedError

    @abstractmethod
    def run(self, prompt: str) -> AgentResult:
        """Execute ``prompt`` and return a captured :class:`AgentResult`.

        Implementations must always populate ``stdout``, ``stderr``, ``exit_code``,
        ``started_at``, and ``finished_at`` -- including on failure. A non-zero exit
        code is a result to record and report, not an error to raise.
        """
        raise NotImplementedError
