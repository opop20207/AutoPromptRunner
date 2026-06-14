"""Checkpoint / rollback routes. Thin handlers over autoprompt_runner.checkpoints.

Checkpoints capture the read-only Git state of a run's workspace before execution. Rollback is
explicit and non-automatic: it requires ``confirm=true`` (400 otherwise) and refuses an unsafe
rollback unless ``force=true`` (409). Nothing here deletes files or runs a Git command except
the single guarded ``git reset --hard`` inside ``checkpoints.rollback_checkpoint``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import List

from fastapi import APIRouter, Body, Depends, HTTPException

from ... import checkpoints, storage
from ..dependencies import get_db_path
from ..schemas import (
    CheckpointResponse,
    RollbackPlanResponse,
    RollbackRequest,
    RollbackResultResponse,
)

router = APIRouter(prefix="/checkpoints", tags=["checkpoints"])

# Map a CheckpointError kind to an HTTP status code.
_STATUS = {"not_found": 404, "not_confirmed": 400, "unsafe": 409}


def _http(exc: checkpoints.CheckpointError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.kind, 400), detail=str(exc))


@router.get("/runs/{run_id}", response_model=List[CheckpointResponse])
def list_checkpoints_for_run(run_id: int, db_path: str = Depends(get_db_path)) -> List[CheckpointResponse]:
    if storage.get_run(db_path, run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return [CheckpointResponse(**asdict(cp)) for cp in checkpoints.list_checkpoints(db_path, run_id)]


@router.get("/{checkpoint_id}", response_model=CheckpointResponse)
def get_checkpoint(checkpoint_id: int, db_path: str = Depends(get_db_path)) -> CheckpointResponse:
    checkpoint = checkpoints.get_checkpoint(db_path, checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail=f"checkpoint {checkpoint_id} not found")
    return CheckpointResponse(**asdict(checkpoint))


@router.get("/{checkpoint_id}/rollback-plan", response_model=RollbackPlanResponse)
def rollback_plan(checkpoint_id: int, db_path: str = Depends(get_db_path)) -> RollbackPlanResponse:
    try:
        plan = checkpoints.build_rollback_plan(db_path, checkpoint_id)
    except checkpoints.CheckpointError as exc:
        raise _http(exc)
    return RollbackPlanResponse(**asdict(plan))


@router.post("/{checkpoint_id}/rollback", response_model=RollbackResultResponse)
def rollback_checkpoint(
    checkpoint_id: int,
    body: RollbackRequest = Body(default=RollbackRequest()),
    db_path: str = Depends(get_db_path),
) -> RollbackResultResponse:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="rollback requires confirm=true")
    try:
        result = checkpoints.rollback_checkpoint(db_path, checkpoint_id, confirm=True, force=body.force)
    except checkpoints.CheckpointError as exc:
        raise _http(exc)
    return RollbackResultResponse(**asdict(result))
