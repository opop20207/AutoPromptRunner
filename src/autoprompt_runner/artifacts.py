"""Run artifacts: types and capture helpers.

An artifact is a recorded piece of a step's context -- the Git state around the step
(read-only) and the runner's stdout/stderr. This module defines the artifact types and
builds the payloads from :mod:`git_utils`; persistence lives in ``storage.py`` and the
orchestration (when to capture) lives in ``RunService``. No subprocess or provider
logic lives here beyond the read-only Git helpers it calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List

from . import git_utils


class ArtifactType(str, Enum):
    """The kinds of artifact recorded for a step."""

    GIT_STATUS_BEFORE = "git_status_before"
    GIT_STATUS_AFTER = "git_status_after"
    GIT_DIFF = "git_diff"
    GIT_DIFF_STAT = "git_diff_stat"
    CHANGED_FILES = "changed_files"
    RUNNER_STDOUT = "runner_stdout"
    RUNNER_STDERR = "runner_stderr"
    GIT_SKIPPED = "git_skipped"


@dataclass
class ArtifactPayload:
    """A type + content pair ready to be persisted as an artifact."""

    type: str
    content: str


def workspace_is_git(workspace) -> bool:
    """Return True if ``workspace`` is set and is a Git repository."""
    return bool(workspace) and git_utils.is_git_repository(workspace)


def capture_git_status(workspace: str) -> str:
    """Return the porcelain Git status of ``workspace`` (read-only)."""
    return git_utils.get_git_status(workspace)


def collect_post_step_git_artifacts(workspace: str, status_before: str, status_after: str) -> List[ArtifactPayload]:
    """Build the Git artifacts for a completed step (all read-only captures)."""
    diff = git_utils.get_git_diff(workspace)
    diff_stat = git_utils.get_git_diff_stat(workspace)
    changed_files = git_utils.get_changed_files(workspace)
    return [
        ArtifactPayload(ArtifactType.GIT_STATUS_BEFORE.value, status_before or ""),
        ArtifactPayload(ArtifactType.GIT_STATUS_AFTER.value, status_after or ""),
        ArtifactPayload(ArtifactType.GIT_DIFF.value, diff),
        ArtifactPayload(ArtifactType.GIT_DIFF_STAT.value, diff_stat),
        ArtifactPayload(ArtifactType.CHANGED_FILES.value, "\n".join(changed_files)),
    ]


def runner_output_artifacts(stdout: str, stderr: str) -> List[ArtifactPayload]:
    """Build the runner stdout/stderr artifacts for a step."""
    return [
        ArtifactPayload(ArtifactType.RUNNER_STDOUT.value, stdout or ""),
        ArtifactPayload(ArtifactType.RUNNER_STDERR.value, stderr or ""),
    ]


def git_skipped_artifact(reason: str) -> ArtifactPayload:
    """Build a compact warning artifact for when Git capture was skipped."""
    return ArtifactPayload(ArtifactType.GIT_SKIPPED.value, reason)
