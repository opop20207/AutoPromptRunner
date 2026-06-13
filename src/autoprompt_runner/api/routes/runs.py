"""Run + artifact routes. Thin handlers over the existing run service and storage."""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ... import locks, queue, safety, storage
from ...models import Approval, Artifact, QueueJob, RunLock, StepExecutionReport, StoredRun, StoredStep
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
_APPROVE_STATUS = {"not_found": 404, "terminal": 409, "no_pending": 400, "locked": 409}
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


class LockResponse(BaseModel):
    id: int
    workspace_path: str
    run_id: int
    status: str
    owner: Optional[str] = None
    created_at: str
    updated_at: str
    expires_at: Optional[str] = None


def _lock_response(lock: RunLock) -> LockResponse:
    return LockResponse(
        id=lock.id, workspace_path=lock.workspace_path, run_id=lock.run_id, status=lock.status,
        owner=lock.owner, created_at=lock.created_at, updated_at=lock.updated_at, expires_at=lock.expires_at,
    )


class QueueJobResponse(BaseModel):
    id: int
    run_id: int
    status: str
    priority: int
    attempts: int
    max_attempts: int
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    last_error: Optional[str] = None


def _queue_response(job: QueueJob) -> QueueJobResponse:
    return QueueJobResponse(
        id=job.id, run_id=job.run_id, status=job.status, priority=job.priority,
        attempts=job.attempts, max_attempts=job.max_attempts, created_at=job.created_at,
        started_at=job.started_at, finished_at=job.finished_at, last_error=job.last_error,
    )


def _queue_fields(db_path: str, run_id: int) -> tuple:
    """Return ``(queue_status, queue_job_id)`` for a run, or ``(None, None)`` if not queued."""
    job = storage.get_job_by_run_id(db_path, run_id)
    if job is None:
        return None, None
    return job.status, job.id


def _short(text: Optional[str], limit: int = _PREVIEW_LIMIT) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)] + "..."


def _run_summary(db_path: str, run: StoredRun) -> RunSummaryResponse:
    queue_status, queue_job_id = _queue_fields(db_path, run.id)
    return RunSummaryResponse(
        id=run.id, status=run.status, provider=run.provider,
        created_at=run.created_at, prompt=run.root_prompt,
        queue_status=queue_status, queue_job_id=queue_job_id,
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
        warnings=[
            artifact.content or ""
            for artifact in storage.list_artifacts_for_run(
                db_path, report.run_id, artifact_type=safety.SAFETY_WARNING_ARTIFACT
            )
        ],
        queue_status=_queue_fields(db_path, report.run_id)[0],
        queue_job_id=_queue_fields(db_path, report.run_id)[1],
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
            template=body.template,
            goal=body.goal,
            extra_context=body.extra_context,
            worktree=body.worktree,
        )
    except RunInputError as exc:
        raise HTTPException(status_code=404 if exc.kind == "not_found" else 400, detail=str(exc))

    service = RunService(db_path)
    if body.queued:
        # Create the run quickly and enqueue it for a background worker; return at once.
        try:
            run_id = service.create_run_only(
                prompt=prompt,
                provider=settings.provider,
                max_loops=settings.max_loops,
                require_approval=settings.require_approval,
                workspace=settings.workspace,
                timeout_seconds=settings.timeout_seconds,
            )
            queue.enqueue(db_path, run_id)
        except (RunServiceError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return _run_summary(db_path, storage.get_run(db_path, run_id))

    try:
        report = service.create_and_execute_run(
            prompt=prompt,
            provider=settings.provider,
            max_loops=settings.max_loops,
            require_approval=settings.require_approval,
            workspace=settings.workspace,
            timeout_seconds=settings.timeout_seconds,
        )
    except RunServiceError as exc:
        raise HTTPException(status_code=409 if exc.kind == "locked" else 400, detail=str(exc))
    return _summary_from_report(db_path, report, body.show_next_prompt)


@router.get("/runs", response_model=List[RunSummaryResponse])
def list_runs(limit: int = 20, db_path: str = Depends(get_db_path)) -> List[RunSummaryResponse]:
    return [_run_summary(db_path, run) for run in storage.list_runs(db_path, limit=limit)]


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def get_run(run_id: int, db_path: str = Depends(get_db_path)) -> RunDetailResponse:
    run = storage.get_run(db_path, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    steps = storage.get_steps_for_run(db_path, run_id)
    pending = storage.get_pending_approval(db_path, run_id)
    run_artifacts = storage.list_artifacts_for_run(db_path, run_id)
    queue_status, queue_job_id = _queue_fields(db_path, run_id)
    return RunDetailResponse(
        id=run.id, status=run.status, provider=run.provider, workspace=run.workspace,
        prompt=run.root_prompt, max_loops=run.max_loops, require_approval=run.require_approval,
        created_at=run.created_at, finished_at=run.finished_at,
        steps=[_step_response(step) for step in steps],
        pending_approval=_approval_response(pending) if pending is not None else None,
        artifacts=[_artifact_summary(artifact) for artifact in run_artifacts],
        queue_status=queue_status, queue_job_id=queue_job_id,
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


@router.get("/locks", response_model=List[LockResponse])
def list_locks(db_path: str = Depends(get_db_path)) -> List[LockResponse]:
    locks.expire_locks(db_path)  # reflect any TTL expiry before listing
    return [_lock_response(lock) for lock in storage.list_locks(db_path)]


@router.post("/locks/{run_id}/release")
def release_lock(run_id: int, db_path: str = Depends(get_db_path)) -> Dict[str, object]:
    released = locks.release_lock(db_path, run_id)
    return {"run_id": run_id, "released": released}


@router.get("/queue", response_model=List[QueueJobResponse])
def list_queue(db_path: str = Depends(get_db_path)) -> List[QueueJobResponse]:
    return [_queue_response(job) for job in storage.list_queue(db_path)]


@router.post("/queue/{run_id}/cancel")
def cancel_queue_job(run_id: int, db_path: str = Depends(get_db_path)) -> Dict[str, object]:
    result = queue.cancel(db_path, run_id)
    if result == queue.CANCEL_CANCELLED:
        return {"run_id": run_id, "cancelled": True}
    if result == queue.CANCEL_RUNNING:
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id} job is already running; process cancellation is not implemented yet",
        )
    if result == queue.CANCEL_NOT_FOUND:
        raise HTTPException(status_code=404, detail=f"no queue job for run {run_id}")
    raise HTTPException(status_code=400, detail=f"run {run_id} job is not cancellable (already finished)")
