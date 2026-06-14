"""Run result review and the explicit local-commit workflow.

After a run succeeds, this module lets a user **review** the workspace changes (changed files,
diff stat, safety warnings, the latest checkpoint, a rule-based commit message, and any
readiness blockers) and then create a **local Git commit** -- only after explicit confirmation.

It is deliberately conservative and **local only**: it never pushes, never opens a pull request,
never creates a release, and never runs a destructive Git command (no reset / clean / checkout /
merge / rebase / pull). It stages only the selected changed files, never secret-like files, and
refuses to commit when readiness blockers exist. Commit messages are **rule-based** (built from
the run's root prompt and changed files) -- no external AI call -- and never include file
contents, secrets, or large stdout/stderr.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import events, git_utils, locks, safety, storage
from .models import RunCommit
from .state import RunStatus

# Commit record statuses (mirror storage constants for importers).
COMMIT_PROPOSED = storage.COMMIT_PROPOSED
COMMIT_COMMITTED = storage.COMMIT_COMMITTED
COMMIT_FAILED = storage.COMMIT_FAILED
COMMIT_SKIPPED = storage.COMMIT_SKIPPED

# Artifact type and event type recorded for a successful local commit.
ARTIFACT_COMMIT = "commit"
EVENT_COMMIT_COMMITTED = "commit_committed"

_SUBJECT_MAX = 72
_FALLBACK_SUBJECT = "Update AutoPromptRunner run changes"
_NO_CHANGES_BLOCKER = "no changed files to commit"

# Run statuses from which a local commit is not allowed (mapped to a readiness blocker).
_STATUS_BLOCKERS = {
    RunStatus.RUNNING.value: "run is still RUNNING",
    RunStatus.WAITING_APPROVAL.value: "run is waiting for approval",
    RunStatus.STOPPED.value: "run was stopped",
}


class CommitError(Exception):
    """Raised for commit-workflow problems.

    ``kind`` is ``"not_found"`` (run missing), ``"not_confirmed"`` (commit attempted without
    explicit confirmation), ``"no_changes"`` (nothing to commit), or ``"blocked"`` (readiness
    blockers exist). Callers map it to a CLI exit code or an HTTP status (404 / 400 / 400 / 409).
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class CommitReview:
    """A read-only review of a run's committable changes (no files are modified)."""

    run_id: int
    run_status: str
    workspace_path: Optional[str]
    is_git_repo: bool
    changed_files: List[str]
    git_diff_stat: str
    safety_warnings: List[str]
    checkpoint_id: Optional[int]
    proposed_message: str
    ready: bool
    blockers: List[str] = field(default_factory=list)


@dataclass
class CommitResult:
    """The outcome of an apply (local commit) attempt."""

    run_id: int
    commit_id: Optional[int]
    status: str
    committed: bool
    commit_hash: Optional[str]
    commit_message: Optional[str]
    changed_files: List[str]
    message: str
    error: Optional[str] = None


# -- review + message --------------------------------------------------------


def build_run_commit_review(db_path: str, run_id: int, allow_failed: bool = False) -> CommitReview:
    """Assemble a read-only commit review for ``run_id``. Raises if the run is missing."""
    db_path = storage.init_db(db_path)
    run = storage.get_run(db_path, run_id)
    if run is None:
        raise CommitError("not_found", f"run {run_id} not found")

    workspace = run.workspace
    is_git = bool(workspace) and git_utils.is_git_repository(workspace)
    changed_files = git_utils.get_changed_files(workspace) if is_git else []
    diff_stat = git_utils.get_git_diff_stat(workspace) if is_git else ""
    safety_warnings = safety.build_safety_warnings(changed_files=changed_files, diff_stat=diff_stat)
    checkpoint = storage.get_latest_checkpoint_for_run(db_path, run_id)
    blockers = _commit_blockers(db_path, run, workspace, is_git, changed_files, allow_failed)

    return CommitReview(
        run_id=run_id,
        run_status=run.status,
        workspace_path=workspace,
        is_git_repo=is_git,
        changed_files=changed_files,
        git_diff_stat=diff_stat,
        safety_warnings=safety_warnings,
        checkpoint_id=checkpoint.id if checkpoint is not None else None,
        proposed_message=generate_commit_message(db_path, run_id),
        ready=not blockers,
        blockers=blockers,
    )


def _commit_blockers(db_path, run, workspace, is_git, changed_files, allow_failed) -> List[str]:
    """Compute the readiness blockers for committing ``run``'s changes (see the README)."""
    blockers: List[str] = []

    # Run state must be a finished, committable run.
    if run.status in _STATUS_BLOCKERS:
        blockers.append(_STATUS_BLOCKERS[run.status])
    elif run.status == RunStatus.FAILED.value and not allow_failed:
        blockers.append("run failed (pass --allow-failed / allow_failed=true to override)")
    job = storage.get_job_by_run_id(db_path, run.id)
    if job is not None and job.status in (storage.QUEUE_QUEUED, storage.QUEUE_RUNNING):
        blockers.append("run is queued or running in the worker")

    # Workspace must be a Git repo.
    if not workspace:
        blockers.append("workspace is not set")
    elif not is_git:
        blockers.append("workspace is not a git repository")
    elif not changed_files:
        blockers.append(_NO_CHANGES_BLOCKER)

    # Never commit secret-like files or over a safety blocker.
    secrets = safety.scan_changed_files_for_secrets(changed_files)
    if secrets:
        blockers.append("secret-like files would be committed: " + ", ".join(secrets[:5]))
    if storage.list_artifacts_for_run(db_path, run.id, safety.SAFETY_BLOCKER_ARTIFACT):
        blockers.append("run has a safety blocker artifact")

    # Do not commit while another run holds the workspace lock.
    if workspace:
        lock = locks.active_lock_for_workspace(db_path, workspace)
        if lock is not None and lock.run_id != run.id:
            blockers.append(f"workspace is locked by another run (run #{lock.run_id})")

    return blockers


def generate_commit_message(db_path: str, run_id: int) -> str:
    """Build a rule-based commit message from the run's root prompt and changed files.

    Subject is derived from the root prompt (capped under 72 chars where practical); an optional
    body records the run id, provider, and changed-file count. No external AI, no secrets, and no
    stdout/stderr content.
    """
    run = storage.get_run(db_path, run_id)
    if run is None:
        return _FALLBACK_SUBJECT
    subject = _subject_from_prompt(run.root_prompt)
    is_git = bool(run.workspace) and git_utils.is_git_repository(run.workspace)
    changed = git_utils.get_changed_files(run.workspace) if is_git else []
    body = f"Run #{run_id} via {run.provider}; {len(changed)} file(s) changed."
    return f"{subject}\n\n{body}"


def _subject_from_prompt(root_prompt: Optional[str]) -> str:
    """Derive a compact commit subject from the first line of the root prompt."""
    first = ""
    for line in (root_prompt or "").splitlines():
        if line.strip():
            first = " ".join(line.split())
            break
    if not first:
        return _FALLBACK_SUBJECT
    subject = first[0].upper() + first[1:]
    if len(subject) > _SUBJECT_MAX:
        cut = subject[:_SUBJECT_MAX]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        subject = cut.rstrip(" .,:;-")
    return subject


# -- propose + apply ---------------------------------------------------------


def propose_commit(db_path: str, run_id: int, allow_failed: bool = False) -> RunCommit:
    """Create a PROPOSED commit record (the proposed message + changed files). Does not commit."""
    db_path = storage.init_db(db_path)
    review = build_run_commit_review(db_path, run_id, allow_failed=allow_failed)
    commit_id = storage.create_commit_record(
        db_path,
        run_id=run_id,
        workspace_path=review.workspace_path or "",
        status=COMMIT_PROPOSED,
        commit_message=review.proposed_message,
        changed_files="\n".join(review.changed_files),
    )
    return storage.get_commit_record(db_path, commit_id)


def commit_run_changes(
    db_path: str,
    run_id: int,
    confirm: bool = False,
    message: Optional[str] = None,
    files: Optional[List[str]] = None,
    allow_failed: bool = False,
) -> CommitResult:
    """Create a local Git commit of a run's changes -- explicit and confirm-gated.

    Refuses without ``confirm`` (``CheckpointError`` analog: ``CommitError("not_confirmed")``),
    refuses when there is nothing to commit (``"no_changes"``) or readiness blockers exist
    (``"blocked"``). Stages only the selected (or all safe) changed files -- never secret-like
    files -- and commits the staged index locally. Never pushes. A failed Git commit is reported
    (record ``FAILED``) rather than raised.
    """
    db_path = storage.init_db(db_path)
    run = storage.get_run(db_path, run_id)
    if run is None:
        raise CommitError("not_found", f"run {run_id} not found")
    review = build_run_commit_review(db_path, run_id, allow_failed=allow_failed)

    if confirm is not True:
        raise CommitError("not_confirmed", "commit requires explicit confirmation (confirm=true / --confirm)")
    if review.blockers:
        real_blockers = [b for b in review.blockers if b != _NO_CHANGES_BLOCKER]
        if real_blockers:
            raise CommitError("blocked", "; ".join(review.blockers))
        raise CommitError("no_changes", _NO_CHANGES_BLOCKER)

    workspace = run.workspace
    files_to_stage = _select_files(review.changed_files, files)
    if not files_to_stage:
        raise CommitError("no_changes", "no committable files were selected")

    message_text = (message or "").strip() or review.proposed_message
    commit_id = storage.create_commit_record(
        db_path, run_id=run_id, workspace_path=workspace, status=COMMIT_PROPOSED,
        commit_message=message_text, changed_files="\n".join(files_to_stage),
    )

    add_result = git_utils.git_add_files(workspace, files_to_stage)
    if not add_result.ok:
        return _fail(db_path, run_id, commit_id, message_text, files_to_stage, add_result.stderr or "git add failed")
    if not git_utils.git_has_staged_changes(workspace):
        return _fail(db_path, run_id, commit_id, message_text, files_to_stage, "nothing staged to commit")

    commit_result = git_utils.git_commit(workspace, message_text)
    if not commit_result.ok:
        return _fail(
            db_path, run_id, commit_id, message_text, files_to_stage,
            (commit_result.stderr or commit_result.stdout or "git commit failed").strip(),
        )

    commit_hash = git_utils.git_get_last_commit_hash(workspace)
    storage.mark_commit_committed(db_path, commit_id, commit_hash or "", commit_message=message_text)
    storage.create_artifact(
        db_path, run_id=run_id, artifact_type=ARTIFACT_COMMIT,
        content=f"local commit {(commit_hash or '')[:12]}: {message_text.splitlines()[0]} "
                f"({len(files_to_stage)} file(s)); not pushed",
    )
    _emit(db_path, run_id, EVENT_COMMIT_COMMITTED, message=f"local commit {(commit_hash or '')[:12]}",
          payload={"commit_id": commit_id, "hash": commit_hash, "files": len(files_to_stage)})
    return CommitResult(
        run_id=run_id, commit_id=commit_id, status=COMMIT_COMMITTED, committed=True,
        commit_hash=commit_hash, commit_message=message_text, changed_files=files_to_stage,
        message=f"created local commit {(commit_hash or '')[:12]} ({len(files_to_stage)} file(s)); not pushed",
    )


def _select_files(changed_files: List[str], requested: Optional[List[str]]) -> List[str]:
    """Pick the files to stage: the requested subset (intersected with changed) or all changed.

    Secret-like files are always excluded (defense in depth; they are also a review blocker).
    """
    base = list(changed_files)
    if requested:
        wanted = set(requested)
        base = [f for f in changed_files if f in wanted]
    secret_free = [f for f in base if not safety.scan_changed_files_for_secrets([f])]
    return secret_free


def _fail(db_path, run_id, commit_id, message_text, files, error) -> CommitResult:
    storage.mark_commit_failed(db_path, commit_id, error=error)
    return CommitResult(
        run_id=run_id, commit_id=commit_id, status=COMMIT_FAILED, committed=False,
        commit_hash=None, commit_message=message_text, changed_files=files,
        message="local commit failed", error=error,
    )


# -- query -------------------------------------------------------------------


def list_commits(db_path: str, run_id: int) -> List[RunCommit]:
    """Return commit records for ``run_id``, newest first."""
    return list(reversed(storage.list_commits_for_run(db_path, run_id)))


def get_commit(db_path: str, commit_id: int) -> Optional[RunCommit]:
    """Return the commit record with ``commit_id`` or ``None``."""
    return storage.get_commit_record(db_path, commit_id)


def changed_files_list(record: RunCommit) -> List[str]:
    """Split a commit record's stored ``changed_files`` text into a list."""
    return [line for line in (record.changed_files or "").splitlines() if line.strip()]


def _emit(db_path: str, run_id: int, event_type: str, message=None, payload=None) -> None:
    """Emit a best-effort run event (never breaks the commit workflow)."""
    try:
        events.create_event(db_path, run_id, event_type, message=message, payload=payload)
    except Exception:  # noqa: BLE001
        pass
