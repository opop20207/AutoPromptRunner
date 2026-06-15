"""Claude Code app-target routes. Thin handlers over autoprompt_runner.app_targets.

An app target identifies a specific Claude Code app session/pane to inject prompts into. These
endpoints are pure CRUD + enable/disable; injection happens via the prompt-queue routes.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from ... import app_targets, window_detection
from ..dependencies import get_db_path
from ..schemas import (
    ActiveWindowResponse,
    AppTargetCreateRequest,
    AppTargetResponse,
    AppTargetUpdateRequest,
    VerificationResultResponse,
)

router = APIRouter(prefix="/app-targets", tags=["app-targets"])

_STATUS = {"not_found": 404, "invalid": 400, "duplicate": 409}


def _http(exc: app_targets.AppTargetError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.kind, 400), detail=str(exc))


def _resp(target) -> AppTargetResponse:
    return AppTargetResponse(**asdict(target))


def _window_resp(window) -> ActiveWindowResponse:
    return ActiveWindowResponse(**asdict(window))


@router.post("", response_model=AppTargetResponse)
def create_app_target(body: AppTargetCreateRequest, db_path: str = Depends(get_db_path)) -> AppTargetResponse:
    try:
        target = app_targets.create_target(
            db_path, name=body.name, app_name=body.app_name, target_mode=body.target_mode,
            submit_mode=body.submit_mode, window_title_hint=body.window_title_hint,
            session_label=body.session_label, project_path=body.project_path, worktree_path=body.worktree_path,
            pane_label=body.pane_label, pane_index=body.pane_index, confirm_before_inject=body.confirm_before_inject,
            target_kind=body.target_kind, verification_mode=body.verification_mode,
            expected_window_title=body.expected_window_title, expected_app_name=body.expected_app_name,
            expected_session_label=body.expected_session_label, expected_project_path=body.expected_project_path,
            expected_pane_label=body.expected_pane_label, expected_pane_index=body.expected_pane_index,
        )
    except app_targets.AppTargetError as exc:
        raise _http(exc)
    return _resp(target)


@router.get("", response_model=List[AppTargetResponse])
def list_app_targets(db_path: str = Depends(get_db_path)) -> List[AppTargetResponse]:
    return [_resp(t) for t in app_targets.list_targets(db_path)]


@router.get("/active-window", response_model=ActiveWindowResponse)
def get_active_window(db_path: str = Depends(get_db_path)) -> ActiveWindowResponse:
    # Registered before /{target_id} so the literal path is not parsed as an id.
    return _window_resp(window_detection.get_active_window_info())


@router.post("/{target_id}/verify", response_model=VerificationResultResponse)
def verify_app_target(target_id: int, db_path: str = Depends(get_db_path)) -> VerificationResultResponse:
    if app_targets.get_target(db_path, target_id) is None:
        raise HTTPException(status_code=404, detail=f"app target {target_id} not found")
    result = app_targets.verify_app_target(db_path, target_id)
    return VerificationResultResponse(
        status=result.status, message=result.message,
        window=_window_resp(result.window) if result.window is not None else None,
        matched=result.matched, summary=window_detection.safe_window_summary(result.window),
    )


@router.get("/{target_id}", response_model=AppTargetResponse)
def get_app_target(target_id: int, db_path: str = Depends(get_db_path)) -> AppTargetResponse:
    target = app_targets.get_target(db_path, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"app target {target_id} not found")
    return _resp(target)


@router.patch("/{target_id}", response_model=AppTargetResponse)
def update_app_target(
    target_id: int, body: AppTargetUpdateRequest, db_path: str = Depends(get_db_path)
) -> AppTargetResponse:
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    try:
        target = app_targets.update_target(db_path, target_id, **fields)
    except app_targets.AppTargetError as exc:
        raise _http(exc)
    return _resp(target)


@router.post("/{target_id}/enable", response_model=AppTargetResponse)
def enable_app_target(target_id: int, db_path: str = Depends(get_db_path)) -> AppTargetResponse:
    try:
        target = app_targets.enable_target(db_path, target_id)
    except app_targets.AppTargetError as exc:
        raise _http(exc)
    return _resp(target)


@router.post("/{target_id}/disable", response_model=AppTargetResponse)
def disable_app_target(target_id: int, db_path: str = Depends(get_db_path)) -> AppTargetResponse:
    try:
        target = app_targets.disable_target(db_path, target_id)
    except app_targets.AppTargetError as exc:
        raise _http(exc)
    return _resp(target)


@router.delete("/{target_id}")
def delete_app_target(target_id: int, db_path: str = Depends(get_db_path)) -> dict:
    try:
        app_targets.delete_target(db_path, target_id)
    except app_targets.AppTargetError as exc:
        raise _http(exc)
    return {"deleted": target_id}
