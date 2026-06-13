"""Run + artifact routes. Thin handlers over the existing run service and storage."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ... import storage
from ...models import Approval, Artifact, StepExecutionReport, StoredRun, StoredStep
from ...services.run_service import RunInputError, RunService, RunServiceError, resolve_run_inputs
from ..dependencies import get_db_path
from ..schemas import (
    ApprovalResponse,
    ArtifactDetailResponse,
    ArtifactSummaryResponse,
    RunCreateRequest,
    RunDetailResponse,
    RunSummaryResponse,
    StepResponse,
)

router = APIRouter(tags=["runs"])

_PREVIEW_LIMIT = 80

# Maps RunServiceError.kind to an HTTP status for the approve/reject endpoints.
_APPROVE_STATUS = {"not_found": 404, "terminal": 409, "no_pending": 400}
_REJECT_STATUS = {"not_found": 404, "no_pending": 400}


class RunLogsResponse(BaseModel):
    run_id: int
    status: str
    generated_at: str
    latest_step_id: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    stdout_artifact_id: Optional[int] = None
    stderr_artifact_id: Optional[int] = None


def _short(text: Optional[str], limit: int = _PREVIEW_LIMIT) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)] + "..."


def _run_summary(run: StoredRun) -> RunSummaryResponse:
    return RunSummaryResponse(
        id=run.id, status=run.status, provider=run.provider,
        created_at=run.created_at, prompt=run.root_prompt,
    )


def _summary_from_report(db_path: str, report: StepExecutionReport, show_next_prompt: bool) -> RunSummaryResponse:
    run = storage.get_run(db_path, report.run_id)
    next_prompt: Optional[str] = None
    if report.next_prompt:
        next_prompt = report.next_prompt if show_next_prompt else _short(report.next_prompt)
    return RunSummaryResponse(
        id=report.run_id,
        status=report.run_status,
        provider=report.provider,
        created_at=run.created_at if run is not None else "",
        prompt=run.root_prompt if run is not None else "",
        next_prompt=next_prompt,
        approval_id=report.approval_id,
        step_id=report.step_id,
        exit_code=report.exit_code,
        message=report.message,
    )


def _step_response(step: StoredStep) -> StepResponse:
    return StepResponse(
        id=step.id, loop_index=step.loop_index, status=step.status, prompt=step.prompt,
        exit_code=step.exit_code, stdout=step.stdout, stderr=step.stderr,
        next_prompt=step.next_prompt, started_at=step.started_at, finished_at=step.finished_at,
    )


def _approval_response(approval: Approval) -> ApprovalResponse:
    return ApprovalResponse(
        id=approval.id, run_id=approval.run_id, step_id=approval.step_id,
        next_prompt=approval.next_prompt, status=approval.status,
        created_at=approval.created_at, decided_at=approval.decided_at,
    )


def _artifact_summary(artifact: Artifact) -> ArtifactSummaryResponse:
    return ArtifactSummaryResponse(
        id=artifact.id, run_id=artifact.run_id, step_id=artifact.step_id,
        type=artifact.type, created_at=artifact.created_at, preview=_short(artifact.content),
    )


def _artifact_detail(artifact: Artifact) -> ArtifactDetailResponse:
    return ArtifactDetailResponse(
        id=artifact.id, run_id=artifact.run_id, step_id=artifact.step_id, type=artifact.type,
        content=artifact.content, path=artifact.path, created_at=artifact.created_at,
    )


@router.post("/runs", response_model=RunSummaryResponse)
def create_run(body: RunCreateRequest, db_path: str = Depends(get_db_path)) -> RunSummaryResponse:
    try:
        prompt, settings = resolve_run_inputs(
            db_path,
            prompt=body.prompt,
            project=body.project,
            provider=body.provider,
            workspace=body.workspace,
            max_loops=body.max_loops,
            timeout_seconds=body.timeout_seconds,
            no_approval=not body.require_approval,
        )
    except RunInputError as exc:
        raise HTTPException(status_code=404 if exc.kind == "not_found" else 400, detail=str(exc))
    try:
        report = RunService(db_path).start(
            prompt=prompt,
            provider=settings.provider,
            max_loops=settings.max_loops,
            require_approval=settings.require_approval,
            workspace=settings.workspace,
            timeout_seconds=settings.timeout_seconds,
        )
    except RunServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _summary_from_report(db_path, report, body.show_next_prompt)


@router.get("/runs", response_model=List[RunSummaryResponse])
def list_runs(limit: int = 20, db_path: str = Depends(get_db_path)) -> List[RunSummaryResponse]:
    return [_run_summary(run) for run in storage.list_runs(db_path, limit=limit)]


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def get_run(run_id: int, db_path: str = Depends(get_db_path)) -> RunDetailResponse:
    run = storage.get_run(db_path, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    steps = storage.get_steps_for_run(db_path, run_id)
    pending = storage.get_pending_approval(db_path, run_id)
    run_artifacts = storage.list_artifacts_for_run(db_path, run_id)
    return RunDetailResponse(
        id=run.id, status=run.status, provider=run.provider, workspace=run.workspace,
        prompt=run.root_prompt, max_loops=run.max_loops, require_approval=run.require_approval,
        created_at=run.created_at, finished_at=run.finished_at,
        steps=[_step_response(step) for step in steps],
        pending_approval=_approval_response(pending) if pending is not None else None,
        artifacts=[_artifact_summary(artifact) for artifact in run_artifacts],
    )


@router.post("/runs/{run_id}/approve-next", response_model=RunSummaryResponse)
def approve_next(
    run_id: int,
    show_next_prompt: bool = False,
    db_path: str = Depends(get_db_path),
) -> RunSummaryResponse:
    try:
        report = RunService(db_path).approve_and_continue(run_id)
    except RunServiceError as exc:
        raise HTTPException(status_code=_APPROVE_STATUS.get(exc.kind, 400), detail=str(exc))
    return _summary_from_report(db_path, report, show_next_prompt)


@router.post("/runs/{run_id}/reject-next", response_model=RunSummaryResponse)
def reject_next(run_id: int, db_path: str = Depends(get_db_path)) -> RunSummaryResponse:
    try:
        report = RunService(db_path).reject(run_id)
    except RunServiceError as exc:
        raise HTTPException(status_code=_REJECT_STATUS.get(exc.kind, 400), detail=str(exc))
    return _summary_from_report(db_path, report, show_next_prompt=False)


@router.get("/runs/{run_id}/artifacts", response_model=List[ArtifactSummaryResponse])
def run_artifacts(
    run_id: int,
    artifact_type: Optional[str] = Query(default=None, alias="type"),
    db_path: str = Depends(get_db_path),
) -> List[ArtifactSummaryResponse]:
    if storage.get_run(db_path, run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    items = storage.list_artifacts_for_run(db_path, run_id, artifact_type=artifact_type)
    return [_artifact_summary(artifact) for artifact in items]


@router.get("/artifacts/{artifact_id}", response_model=ArtifactDetailResponse)
def get_artifact(artifact_id: int, db_path: str = Depends(get_db_path)) -> ArtifactDetailResponse:
    artifact = storage.get_artifact(db_path, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"artifact {artifact_id} not found")
    return _artifact_detail(artifact)


@router.get("/runs/{run_id}/logs", response_model=RunLogsResponse)
def run_logs(run_id: int, db_path: str = Depends(get_db_path)) -> RunLogsResponse:
    logs = storage.get_run_logs(db_path, run_id)
    if logs is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return RunLogsResponse(**logs)
