"""Local-commit workflow routes. Thin handlers over autoprompt_runner.commits.

Review a successful run's workspace changes, propose a rule-based commit message, and create a
**local Git commit** after explicit confirmation. This never pushes, never opens a PR, and never
runs a destructive Git command. Commit requires ``confirm=true`` (400 otherwise), returns 400
when there is nothing to commit, 409 when readiness blockers exist, and 404 for a missing run.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import List

from fastapi import APIRouter, Body, Depends, HTTPException

from ... import commits, storage
from ..dependencies import get_db_path
from ..schemas import (
    CommitApplyRequest,
    CommitProposeRequest,
    CommitRecordResponse,
    CommitResultResponse,
    CommitReviewResponse,
)

router = APIRouter(prefix="/commits", tags=["commits"])

# Map a CommitError kind to an HTTP status code.
_STATUS = {"not_found": 404, "not_confirmed": 400, "no_changes": 400, "blocked": 409}


def _http(exc: commits.CommitError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.kind, 400), detail=str(exc))


def _record_response(record) -> CommitRecordResponse:
    data = asdict(record)
    data["changed_files"] = commits.changed_files_list(record)
    return CommitRecordResponse(**data)


@router.get("/runs/{run_id}/review", response_model=CommitReviewResponse)
def commit_review(run_id: int, allow_failed: bool = False, db_path: str = Depends(get_db_path)) -> CommitReviewResponse:
    try:
        review = commits.build_run_commit_review(db_path, run_id, allow_failed=allow_failed)
    except commits.CommitError as exc:
        raise _http(exc)
    return CommitReviewResponse(**asdict(review))


@router.post("/runs/{run_id}/propose", response_model=CommitRecordResponse)
def commit_propose(
    run_id: int,
    body: CommitProposeRequest = Body(default=CommitProposeRequest()),
    db_path: str = Depends(get_db_path),
) -> CommitRecordResponse:
    try:
        record = commits.propose_commit(db_path, run_id, allow_failed=body.allow_failed)
    except commits.CommitError as exc:
        raise _http(exc)
    return _record_response(record)


@router.post("/runs/{run_id}/apply", response_model=CommitResultResponse)
def commit_apply(
    run_id: int,
    body: CommitApplyRequest = Body(default=CommitApplyRequest()),
    db_path: str = Depends(get_db_path),
) -> CommitResultResponse:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="commit requires confirm=true")
    try:
        result = commits.commit_run_changes(
            db_path, run_id, confirm=True, message=body.message, files=body.files or None,
            allow_failed=body.allow_failed,
        )
    except commits.CommitError as exc:
        raise _http(exc)
    return CommitResultResponse(**asdict(result))


@router.get("/runs/{run_id}", response_model=List[CommitRecordResponse])
def list_commits_for_run(run_id: int, db_path: str = Depends(get_db_path)) -> List[CommitRecordResponse]:
    if storage.get_run(db_path, run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return [_record_response(rec) for rec in commits.list_commits(db_path, run_id)]
