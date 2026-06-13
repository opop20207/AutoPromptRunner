"""Service layer for AutoPromptRunner.

Holds the prompt-loop orchestration (``RunService``) and the deterministic
next-prompt generator (``PromptGenerator``). These coordinate the runners, storage,
and approval gate; they call no external AI APIs and use no network access.
"""

from __future__ import annotations

from .prompt_generator import PromptGenerator
from .run_service import RunService, RunServiceError

__all__ = ["PromptGenerator", "RunService", "RunServiceError"]
