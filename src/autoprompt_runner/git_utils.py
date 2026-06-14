"""Read-only Git helpers.

These functions run Git through ``subprocess.run`` (never a shell) inside a workspace
directory and capture stdout/stderr/exit code. They are strictly read-only: a denylist
rejects any mutating subcommand (add, commit, reset, checkout, clean, push, pull,
merge, rebase, ...). They never modify the repository, read secrets, or print anything.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence

# Mutating Git subcommands that must never be run by this tool.
_DESTRUCTIVE_SUBCOMMANDS = frozenset(
    {
        "add", "commit", "reset", "checkout", "clean", "push", "pull", "merge",
        "rebase", "restore", "switch", "rm", "mv", "stash", "revert", "cherry-pick",
        "apply", "am", "fetch", "gc", "prune", "tag", "branch", "init", "config",
    }
)

_EXIT_NOT_FOUND = 127
_EXIT_TIMEOUT = 124
_EXIT_ERROR = 1


@dataclass
class GitCommandResult:
    """The captured outcome of one read-only Git command."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_git_command(path: str, args: Sequence[str], timeout_seconds: int = 30) -> GitCommandResult:
    """Run ``git <args>`` in ``path`` and capture the result.

    Raises ``ValueError`` if ``args`` names a mutating/destructive subcommand. Missing
    git, timeouts, and OS errors are returned as a non-zero ``GitCommandResult`` rather
    than raised, so callers can record them as artifacts.
    """
    for token in args:
        if token in _DESTRUCTIVE_SUBCOMMANDS:
            raise ValueError(f"refusing to run a non-read-only git command: git {token}")

    argv = ["git", *args]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=path,
            shell=False,
            check=False,
        )
        return GitCommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")
    except FileNotFoundError:
        return GitCommandResult(_EXIT_NOT_FOUND, "", "git: command not found")
    except subprocess.TimeoutExpired:
        return GitCommandResult(_EXIT_TIMEOUT, "", f"git: timed out after {timeout_seconds}s")
    except OSError as exc:
        return GitCommandResult(_EXIT_ERROR, "", f"git: failed: {exc}")


def is_git_repository(path: str) -> bool:
    """Return True if ``path`` is inside a Git work tree."""
    if not path:
        return False
    result = run_git_command(path, ["rev-parse", "--is-inside-work-tree"])
    return result.ok and result.stdout.strip() == "true"


def get_git_status(path: str) -> str:
    """Return ``git status --porcelain`` output (empty if clean or on error)."""
    return run_git_command(path, ["status", "--porcelain"]).stdout


def get_git_diff(path: str) -> str:
    """Return the working-tree diff (vs HEAD when a commit exists, else unstaged)."""
    result = run_git_command(path, ["diff", "HEAD"])
    if not result.ok:
        result = run_git_command(path, ["diff"])
    return result.stdout


def get_git_diff_stat(path: str) -> str:
    """Return the diff summary (``--stat``), vs HEAD when possible."""
    result = run_git_command(path, ["diff", "--stat", "HEAD"])
    if not result.ok:
        result = run_git_command(path, ["diff", "--stat"])
    return result.stdout


def get_changed_files(path: str) -> List[str]:
    """Return the list of changed/untracked file paths from porcelain status."""
    status = get_git_status(path)
    files: List[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:]
        if " -> " in entry:  # renamed: "old -> new"
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip().strip('"')
        if entry:
            files.append(entry)
    return files


# -- checkpoint helpers ------------------------------------------------------
# Read-only state capture for run checkpoints, plus the single guarded destructive
# command (git reset --hard) used only by the explicit rollback path.


def get_git_head(path: str) -> Optional[str]:
    """Return the current HEAD commit hash (read-only), or ``None`` if unavailable."""
    result = run_git_command(path, ["rev-parse", "HEAD"])
    head = result.stdout.strip()
    return head if result.ok and head else None


def get_git_branch(path: str) -> Optional[str]:
    """Return the current branch name (``HEAD`` when detached), or ``None`` if unavailable."""
    result = run_git_command(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = result.stdout.strip()
    return branch if result.ok and branch else None


def get_git_status_porcelain(path: str) -> str:
    """Return ``git status --porcelain`` output (read-only; alias of :func:`get_git_status`)."""
    return get_git_status(path)


def is_git_dirty(path: str) -> bool:
    """Return True if the work tree has uncommitted changes (read-only)."""
    return bool(get_git_status_porcelain(path).strip())


def git_reset_hard(path: str, target_ref: str, confirm: bool = False, timeout_seconds: int = 30) -> GitCommandResult:
    """Run ``git reset --hard <target_ref>`` in ``path`` -- DESTRUCTIVE.

    This is the only mutating Git command in the codebase. It must be called *only* from
    :func:`autoprompt_runner.checkpoints.rollback_checkpoint`, and only after the user has
    explicitly confirmed: the explicit ``confirm`` guard raises ``ValueError`` unless it is
    exactly ``True``. It discards uncommitted changes in the work tree, so callers must have
    warned the user and recorded a safety artifact first. It never runs ``git clean``, never
    deletes files outside Git's own reset, and never pushes/pulls/merges. The target ref is
    validated (non-empty, not an option flag) to avoid argument injection. Missing git,
    timeouts, and OS errors return a non-zero result rather than raising.
    """
    if confirm is not True:
        raise ValueError("git_reset_hard requires confirm=True (it is destructive)")
    ref = (target_ref or "").strip()
    if not ref or ref.startswith("-"):
        raise ValueError("git_reset_hard requires a valid target ref")
    return _run_mutating_git(path, ["reset", "--hard", ref], timeout_seconds)


def _run_mutating_git(path: str, args: Sequence[str], timeout_seconds: int) -> GitCommandResult:
    """Run a mutating ``git <args>`` directly (bypassing the read-only denylist).

    Used only by the small set of guarded write helpers in this module (reset for rollback,
    add/commit for the local commit workflow). Still never uses a shell. Missing git, timeouts,
    and OS errors return a non-zero result rather than raising.
    """
    argv = ["git", *args]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=path,
            shell=False,
            check=False,
        )
        return GitCommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")
    except FileNotFoundError:
        return GitCommandResult(_EXIT_NOT_FOUND, "", "git: command not found")
    except subprocess.TimeoutExpired:
        return GitCommandResult(_EXIT_TIMEOUT, "", f"git: timed out after {timeout_seconds}s")
    except OSError as exc:
        return GitCommandResult(_EXIT_ERROR, "", f"git: failed: {exc}")


# -- local commit helpers ----------------------------------------------------
# Staging + committing are the only writes here besides rollback's reset. They must be called
# only from autoprompt_runner.commits (the explicit, confirm-gated commit workflow). They never
# push, pull, reset, clean, checkout, merge, or rebase.


def _validate_relative_file(file: str) -> str:
    """Return a safe, repo-relative ``file`` token or raise ValueError.

    Rejects empty paths, option-like tokens (leading ``-``), absolute paths, and ``..`` parent
    traversal so a caller can never stage something outside the workspace or inject a flag.
    """
    name = (file or "").strip()
    if not name or name.startswith("-"):
        raise ValueError(f"invalid file to stage: {file!r}")
    if os.path.isabs(name) or name.startswith("/") or name.startswith("\\"):
        raise ValueError(f"refusing to stage a non-relative path: {file!r}")
    parts = name.replace("\\", "/").split("/")
    if ".." in parts:
        raise ValueError(f"refusing to stage a path with parent traversal: {file!r}")
    return name


def git_add_files(path: str, files: Sequence[str], timeout_seconds: int = 30) -> GitCommandResult:
    """Stage only the explicitly listed ``files`` (``git add -- <files>``).

    Never stages everything (no ``git add -A`` / ``.``). Each path is validated to be a safe
    repo-relative file (see :func:`_validate_relative_file`); an empty list is a ValueError. The
    caller (``autoprompt_runner.commits``) is responsible for excluding secret-like files.
    """
    safe = [_validate_relative_file(f) for f in (files or [])]
    if not safe:
        raise ValueError("git_add_files requires at least one file to stage")
    return _run_mutating_git(path, ["add", "--", *safe], timeout_seconds)


def git_commit(path: str, message: str, timeout_seconds: int = 30) -> GitCommandResult:
    """Create a local commit of the staged index (``git commit -m <message>``).

    Commits only what is staged; never uses ``-a`` / ``--all`` and never pushes. The message is
    passed as a single argument (no shell), so it may be multi-line. A blank message is rejected.
    """
    text = (message or "").strip()
    if not text:
        raise ValueError("git_commit requires a non-empty message")
    return _run_mutating_git(path, ["commit", "-m", message], timeout_seconds)


def git_get_last_commit_hash(path: str) -> Optional[str]:
    """Return the current HEAD commit hash (read-only), or ``None`` if unavailable."""
    return get_git_head(path)


def git_has_staged_changes(path: str) -> bool:
    """Return True if the index has staged changes (read-only ``git diff --cached --quiet``)."""
    # --quiet implies --exit-code: exit 1 means there ARE staged differences.
    return run_git_command(path, ["diff", "--cached", "--quiet"]).returncode == 1


def _porcelain_files(path: str, predicate) -> List[str]:
    """Return porcelain-status paths for which ``predicate(index_char, worktree_char)`` is True."""
    files: List[str] = []
    for line in get_git_status(path).splitlines():
        if len(line) < 4:
            continue
        index_char, worktree_char = line[0], line[1]
        entry = line[3:]
        if " -> " in entry:  # renamed: "old -> new"
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip().strip('"')
        if entry and predicate(index_char, worktree_char):
            files.append(entry)
    return files


def git_get_unstaged_changed_files(path: str) -> List[str]:
    """Return files with unstaged work-tree changes or that are untracked (read-only)."""
    return _porcelain_files(path, lambda x, y: y != " " or (x == "?" and y == "?"))


def git_get_staged_changed_files(path: str) -> List[str]:
    """Return files with staged (index) changes (read-only)."""
    return _porcelain_files(path, lambda x, y: x not in (" ", "?"))
