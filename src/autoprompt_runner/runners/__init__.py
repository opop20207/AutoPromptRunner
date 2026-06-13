"""Runner provider implementations.

Each provider implements the :class:`~autoprompt_runner.runners.base.AgentRunner`
interface so callers never branch on the concrete agent type. ``MockRunner`` (offline,
deterministic) and ``ClaudeCodeRunner`` (the Claude Code CLI) are available; the Codex
runner is a future provider.
"""

from __future__ import annotations

from .base import AgentRunner
from .claude_code import ClaudeCodeRunner
from .mock import MockRunner

__all__ = ["AgentRunner", "ClaudeCodeRunner", "MockRunner"]
