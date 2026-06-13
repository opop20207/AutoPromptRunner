"""Project profile run-settings resolution.

A run's effective settings are resolved by merging, in order of precedence:

1. explicit CLI arguments,
2. the selected project profile (``--project``),
3. the default project profile,
4. built-in defaults.

The CLI passes a single project (the selected one, else the default, else ``None``)
plus the explicit flags; this module computes the resolved settings. It contains no
storage or subprocess logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Project

# Built-in defaults (lowest precedence).
BUILTIN_PROVIDER = "mock"
BUILTIN_MAX_LOOPS = 1
BUILTIN_TIMEOUT_SECONDS = 1800
BUILTIN_REQUIRE_APPROVAL = True


@dataclass
class ResolvedRunSettings:
    """The effective settings for a run after applying precedence."""

    provider: str
    max_loops: int
    require_approval: bool
    timeout_seconds: int
    workspace: Optional[str]


def resolve_run_settings(
    project: Optional[Project],
    *,
    provider: Optional[str] = None,
    max_loops: Optional[int] = None,
    timeout_seconds: Optional[int] = None,
    workspace: Optional[str] = None,
    no_approval: bool = False,
) -> ResolvedRunSettings:
    """Resolve run settings from explicit args, an optional project, and defaults.

    Explicit (non-``None``) arguments win. ``no_approval`` is the only explicit way to
    set ``require_approval`` to ``False``; otherwise the project's value (then the
    built-in default ``True``) is used. For the claude-code provider, the workspace
    falls back to the project's ``repo_path`` when ``--workspace`` is not given.
    """
    resolved_provider = provider or (project.default_provider if project else None) or BUILTIN_PROVIDER

    if max_loops is not None:
        resolved_max_loops = max_loops
    elif project is not None and project.default_max_loops is not None:
        resolved_max_loops = project.default_max_loops
    else:
        resolved_max_loops = BUILTIN_MAX_LOOPS

    if timeout_seconds is not None:
        resolved_timeout = timeout_seconds
    elif project is not None and project.timeout_seconds is not None:
        resolved_timeout = project.timeout_seconds
    else:
        resolved_timeout = BUILTIN_TIMEOUT_SECONDS

    if no_approval:
        resolved_require_approval = False
    elif project is not None:
        resolved_require_approval = project.require_approval
    else:
        resolved_require_approval = BUILTIN_REQUIRE_APPROVAL

    resolved_workspace = workspace
    if resolved_workspace is None and resolved_provider == "claude-code" and project is not None:
        resolved_workspace = project.repo_path

    return ResolvedRunSettings(
        provider=resolved_provider,
        max_loops=resolved_max_loops,
        require_approval=resolved_require_approval,
        timeout_seconds=resolved_timeout,
        workspace=resolved_workspace,
    )
