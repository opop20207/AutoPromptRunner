"""Runner provider implementations.

Each provider implements the :class:`~autoprompt_runner.runners.base.AgentRunner`
interface so callers never branch on the concrete agent type. ``MockRunner`` (offline,
deterministic), ``ClaudeCodeRunner`` (the Claude Code CLI), and ``CodexRunner`` (the
Codex CLI) are all available.
"""

from __future__ import annotations

from .base import AgentRunner
from .claude_code import ClaudeCodeRunner
from .codex import CodexRunner
from .mock import MockRunner

__all__ = ["AgentRunner", "ClaudeCodeRunner", "CodexRunner", "MockRunner"]
