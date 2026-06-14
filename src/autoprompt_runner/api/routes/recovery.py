"""Failure recovery routes. Thin handlers over autoprompt_runner.recovery.

Recovery prompts are generated from stored failure context only (rule-based, no AI); a
recovery run is a new run linked to the source run. The original run's records are not
mutated.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Body, Depends, HTTPException

from ... import recovery, storage
from ...models import RecoveryAttempt
from ..dependencies import get_db_path
from ..schemas import (
    RecoveryAttemptResponse,
    RecoveryDecisionRequest,
    RecoveryExecuteRequest,
    RecoveryListResponse,
    RecoveryProposeRequest,
)

router = APIRouter(prefix="/recovery", tags=["recovery"])

# Map a RecoveryError kind to an HTTP status code.
_STATUS = {"not_found": 404, "not_failed": 400, "rejected": 409, "executed": 409}


def _to_response(attempt: RecoveryAttempt) -> RecoveryAttemptResponse:
    return RecoveryAttemptResponse(**asdict(attempt))


def _http(exc: recovery.RecoveryError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.kind, 400), detail=str(exc))


@router.post("/runs/{run_id}/propose", response_model=RecoveryAttemptResponse)
def propose_recovery(
    run_id: int,
    body: RecoveryProposeRequest = Body(default=RecoveryProposeRequest()),
    db_path: str = Depends(get_db_path),
) -> RecoveryAttemptResponse:
    try:
        attempt = recovery.propose_recovery(db_path, run_id, reason=body.reason)
    except recovery.RecoveryError as exc:
        raise _http(exc)
    return _to_response(attempt)


@router.get("/runs/{run_id}", response_model=RecoveryListResponse)
def list_recoveries_for_run(run_id: int, db_path: str = Depends(get_db_path)) -> RecoveryListResponse:
    if storage.get_run(db_path, run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return RecoveryListResponse(
        recoveries=[_to_response(a) for a in recovery.list_recoveries_for_run(db_path, run_id)]
    )


@router.post("/{recovery_id}/approve", response_model=RecoveryAttemptResponse)
def approve_recovery(recovery_id: int, db_path: str = Depends(get_db_path)) -> RecoveryAttemptResponse:
    try:
        attempt = recovery.approve_recovery(db_path, recovery_id)
    except recovery.RecoveryError as exc:
        raise _http(exc)
    return _to_response(attempt)


@router.post("/{recovery_id}/reject", response_model=RecoveryAttemptResponse)
def reject_recovery(
    recovery_id: int,
    body: RecoveryDecisionRequest = Body(default=RecoveryDecisionRequest()),
    db_path: str = Depends(get_db_path),
) -> RecoveryAttemptResponse:
    try:
        attempt = recovery.reject_recovery(db_path, recovery_id, reason=body.reason)
    except recovery.RecoveryError as exc:
        raise _http(exc)
    return _to_response(attempt)


@router.post("/{recovery_id}/execute", response_model=RecoveryAttemptResponse)
def execute_recovery(
    recovery_id: int,
    body: RecoveryExecuteRequest = Body(default=RecoveryExecuteRequest()),
    db_path: str = Depends(get_db_path),
) -> RecoveryAttemptResponse:
    try:
        result = recovery.execute_recovery(db_path, recovery_id, queued=body.queued)
    except recovery.RecoveryError as exc:
        raise _http(exc)
    return _to_response(result.attempt)


@router.get("", response_model=RecoveryListResponse)
def list_recoveries(db_path: str = Depends(get_db_path)) -> RecoveryListResponse:
    return RecoveryListResponse(recoveries=[_to_response(a) for a in recovery.list_recoveries(db_path)])
