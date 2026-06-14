"""Safe Git worktree helpers for isolated parallel sessions.

Parallel agent sessions must never share one working tree. This module manages isolated
Git worktrees (each on its own branch in its own directory) through a small, strictly
allowlisted set of ``git`` commands run via ``subprocess.run`` (never ``shell=True``):

* ``git worktree list --porcelain``
* ``git worktree add``
* ``git worktree remove``
* ``git branch --show-current``

It never runs ``reset`` / ``clean`` / ``push`` / ``pull`` / ``merge`` / ``rebase`` and
never deletes files manually -- worktree directories are only ever removed via
``git worktree remove``. The module is pure (no database access): names/paths are
validated and containment is enforced so a generated worktree path can never escape the
configured worktrees parent directory.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from . import paths

# Worktree lifecycle statuses.
WORKTREE_ACTIVE = "ACTIVE"
WORKTREE_LOCKED = "LOCKED"
WORKTREE_ARCHIVED = "ARCHIVED"
WORKTREE_STATUSES = (WORKTREE_ACTIVE, WORKTREE_LOCKED, WORKTREE_ARCHIVED)

# Default on-disk location, relative to the .autoprompt state directory:
#   .autoprompt/worktrees/{project_name}/{worktree_name}
_WORKTREES_DIRNAME = "worktrees"

# A worktree name maps to a single path component: letters, digits, '.', '_', '-'.
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Characters/sequences a Git branch ref must not contain (a safe subset of
# git check-ref-format, enforced without spawning a process).
_BRANCH_FORBIDDEN = ("..", "~", "^", ":", "?", "*", "[", "\\", "@{", " ", "\t")

# The only git invocations this module will run, keyed by (arg0, arg1).
_ALLOWED_GIT = frozenset(
    {
        ("worktree", "list"),
        ("worktree", "add"),
        ("worktree", "remove"),
        ("branch", "--show-current"),
    }
)

_GIT_TIMEOUT_SECONDS = 60


class WorktreeError(Exception):
    """Raised for invalid worktree input or a failed/refused git worktree command."""


@dataclass
class GitWorktreeEntry:
    """One entry from ``git worktree list --porcelain``."""

    path: str
    branch: Optional[str] = None
    head: Optional[str] = None
    bare: bool = False
    detached: bool = False


# -- validation --------------------------------------------------------------


def validate_worktree_name(name: str) -> str:
    """Return the cleaned worktree name or raise :class:`WorktreeError`.

    A name must be a single safe path component (letters, digits, ``.`` ``_`` ``-``); it
    may not be empty, ``.``/``..``, or contain a path separator.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise WorktreeError("worktree name must not be empty")
    if cleaned in (".", ".."):
        raise WorktreeError("worktree name must not be '.' or '..'")
    if not _NAME_RE.match(cleaned):
        raise WorktreeError("worktree name may only contain letters, digits, '.', '_' and '-'")
    return cleaned


def validate_branch_name(branch: str) -> str:
    """Return the cleaned branch name or raise :class:`WorktreeError`.

    Enforces a safe subset of ``git check-ref-format`` (no spaces, ``..``, ``~^:?*[\\``,
    ``@{``; no leading ``-`` or ``/``; no trailing ``/`` or ``.lock``; not a lone ``@``).
    """
    cleaned = (branch or "").strip()
    if not cleaned:
        raise WorktreeError("branch name must not be empty")
    if cleaned == "@":
        raise WorktreeError("branch name must not be '@'")
    if cleaned.startswith("-"):
        raise WorktreeError("branch name must not start with '-'")
    if cleaned.startswith("/") or cleaned.endswith("/") or "//" in cleaned:
        raise WorktreeError("branch name must not start/end with '/' or contain '//'")
    if cleaned.endswith(".lock") or cleaned.endswith("."):
        raise WorktreeError("branch name must not end with '.lock' or '.'")
    for token in _BRANCH_FORBIDDEN:
        if token in cleaned:
            raise WorktreeError(f"branch name must not contain '{token.strip() or 'whitespace'}'")
    return cleaned


def is_path_inside_parent(path: str, parent: str) -> bool:
    """Return True if ``path`` resolves to a location strictly inside ``parent``.

    Delegates to :func:`autoprompt_runner.paths.is_subpath` (pathlib-based; Windows-aware
    case-insensitive comparison; different drives are not contained).
    """
    return paths.is_subpath(path, parent)


def safe_path_component(value: str) -> str:
    """Reduce an arbitrary string (e.g. a project name) to one safe path component."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "project"


def default_worktrees_root(db_path: str) -> str:
    """Return the worktrees root next to the database (``<state-dir>/worktrees``)."""
    base = Path(os.path.abspath(db_path)).parent if db_path else Path(os.path.abspath(".autoprompt"))
    return str(base / _WORKTREES_DIRNAME)


def build_worktree_path(base_dir: str, name: str) -> str:
    """Return ``base_dir/<validated-name>`` (the name is validated as a path component)."""
    return str(Path(base_dir) / validate_worktree_name(name))


def prepare_worktree_path(db_path: str, project_name: str, name: str) -> str:
    """Compute the absolute worktree path and enforce it stays inside the worktrees root."""
    root = default_worktrees_root(db_path)
    project_dir = str(Path(root) / safe_path_component(project_name))
    path = os.path.abspath(build_worktree_path(project_dir, name))
    if not is_path_inside_parent(path, root):
        raise WorktreeError(f"computed worktree path escapes the worktrees root: {paths.safe_display_path(path)}")
    return path


# -- git worktree commands ---------------------------------------------------


def _run_git(repo_path: str, args: Sequence[str]) -> subprocess.CompletedProcess:
    """Run an allowlisted ``git`` command in ``repo_path`` (never via a shell)."""
    key = (args[0], args[1]) if len(args) >= 2 else (args[0] if args else "",)
    if key not in _ALLOWED_GIT:
        raise WorktreeError(f"refusing to run git command: git {' '.join(args)}")
    try:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            cwd=repo_path,
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise WorktreeError("git: command not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorktreeError(f"git: timed out after {_GIT_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise WorktreeError(f"git: failed: {exc}") from exc


def _git_message(completed: subprocess.CompletedProcess) -> str:
    return (completed.stderr or "").strip() or (completed.stdout or "").strip() or "unknown error"


def create_git_worktree(
    repo_path: str,
    worktree_path: str,
    branch: str,
    base_branch: Optional[str] = None,
) -> GitWorktreeEntry:
    """Create a new worktree on a new ``branch`` via ``git worktree add -b``.

    The branch starts from ``base_branch`` when given, otherwise the repo's current HEAD.
    Raises :class:`WorktreeError` on invalid input, an existing target path, or a git
    failure. No existing files are deleted and no destructive git command is used.
    """
    branch = validate_branch_name(branch)
    if os.path.exists(worktree_path):
        raise WorktreeError(f"worktree path already exists: {worktree_path}")
    parent = os.path.dirname(os.path.abspath(worktree_path))
    os.makedirs(parent, exist_ok=True)  # create only the parent; git creates the worktree dir

    args = ["worktree", "add", "-b", branch, worktree_path]
    if base_branch:
        args.append(base_branch)
    completed = _run_git(repo_path, args)
    if completed.returncode != 0:
        raise WorktreeError(f"git worktree add failed: {_git_message(completed)}")
    return GitWorktreeEntry(path=os.path.abspath(worktree_path), branch=branch)


def list_git_worktrees(repo_path: str) -> List[GitWorktreeEntry]:
    """Return the repo's worktrees from ``git worktree list --porcelain``."""
    completed = _run_git(repo_path, ["worktree", "list", "--porcelain"])
    if completed.returncode != 0:
        raise WorktreeError(f"git worktree list failed: {_git_message(completed)}")
    return _parse_porcelain(completed.stdout or "")


def remove_git_worktree(repo_path: str, worktree_path: str, force: bool = False) -> None:
    """Remove a worktree using ``git worktree remove`` only (optionally ``--force``)."""
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(worktree_path)
    completed = _run_git(repo_path, args)
    if completed.returncode != 0:
        raise WorktreeError(f"git worktree remove failed: {_git_message(completed)}")


def get_current_branch(repo_path: str) -> Optional[str]:
    """Return the current branch via ``git branch --show-current`` (or ``None``)."""
    completed = _run_git(repo_path, ["branch", "--show-current"])
    if completed.returncode != 0:
        return None
    return (completed.stdout or "").strip() or None


def _parse_porcelain(text: str) -> List[GitWorktreeEntry]:
    """Parse ``git worktree list --porcelain`` output into entries."""
    entries: List[GitWorktreeEntry] = []
    current: Optional[GitWorktreeEntry] = None
    for line in text.splitlines():
        if line.startswith("worktree "):
            current = GitWorktreeEntry(path=line[len("worktree ") :].strip())
            entries.append(current)
        elif current is None:
            continue
        elif line.startswith("HEAD "):
            current.head = line[len("HEAD ") :].strip()
        elif line.startswith("branch "):
            ref = line[len("branch ") :].strip()
            current.branch = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
        elif line.strip() == "bare":
            current.bare = True
        elif line.strip() == "detached":
            current.detached = True
    return entries
