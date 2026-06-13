"""Runner provider implementations.

Each provider implements the :class:`~autoprompt_runner.runners.base.AgentRunner`
interface so callers never branch on the concrete agent type. Only ``MockRunner`` is
available in this step; ``ClaudeCodeRunner`` and ``CodexRunner`` are future providers.
"""

from __future__ import annotations

from .base import AgentRunner
from .mock import MockRunner

__all__ = ["AgentRunner", "MockRunner"]
