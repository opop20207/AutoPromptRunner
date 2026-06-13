"""Worktree routes. Thin handlers over the worktrees module and storage layer."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ... import storage, worktrees
from ...models import Worktree
from ..dependencies import get_db_path
from ..schemas import WorktreeCreateRequest, WorktreeResponse

router = APIRouter(prefix="/worktrees", tags=["worktrees"])


def _to_response(db_path: str, worktree: Worktree) -> WorktreeResponse:
    project = storage.get_project_by_id(db_path, worktree.project_id)
    return WorktreeResponse(
        id=worktree.id,
        project_id=worktree.project_id,
        project=project.name if project is not None else None,
        name=worktree.name,
        branch=worktree.branch,
        path=worktree.path,
        base_branch=worktree.base_branch,
        status=worktree.status,
        created_at=worktree.created_at,
        updated_at=worktree.updated_at,
    )


@router.post("", response_model=WorktreeResponse, status_code=201)
def create_worktree(body: WorktreeCreateRequest, db_path: str = Depends(get_db_path)) -> WorktreeResponse:
    project = storage.get_project_by_name(db_path, body.project)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project '{body.project}' not found")
    if not project.repo_path or not os.path.isdir(project.repo_path):
        raise HTTPException(status_code=400, detail=f"project repo_path does not exist: {project.repo_path}")
    try:
        name = worktrees.validate_worktree_name(body.name)
        branch = worktrees.validate_branch_name(body.branch)
    except worktrees.WorktreeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if storage.get_worktree_by_name(db_path, name) is not None:
        raise HTTPException(status_code=400, detail=f"worktree '{name}' already exists")
    try:
        path = worktrees.prepare_worktree_path(db_path, project.name, name)
        worktrees.create_git_worktree(project.repo_path, path, branch, body.base_branch)
    except worktrees.WorktreeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    worktree_id = storage.create_worktree_record(
        db_path, project_id=project.id, name=name, branch=branch, path=path,
        base_branch=body.base_branch, status=worktrees.WORKTREE_ACTIVE,
    )
    return _to_response(db_path, storage.get_worktree_by_id(db_path, worktree_id))


@router.get("", response_model=List[WorktreeResponse])
def list_worktrees(
    project: Optional[str] = Query(default=None),
    db_path: str = Depends(get_db_path),
) -> List[WorktreeResponse]:
    if project:
        proj = storage.get_project_by_name(db_path, project)
        if proj is None:
            raise HTTPException(status_code=404, detail=f"project '{project}' not found")
        items = storage.list_worktrees_for_project(db_path, proj.id)
    else:
        items = storage.list_worktrees(db_path)
    return [_to_response(db_path, wt) for wt in items]


@router.get("/{worktree_name}", response_model=WorktreeResponse)
def get_worktree(worktree_name: str, db_path: str = Depends(get_db_path)) -> WorktreeResponse:
    wt = storage.get_worktree_by_name(db_path, worktree_name)
    if wt is None:
        raise HTTPException(status_code=404, detail=f"worktree '{worktree_name}' not found")
    return _to_response(db_path, wt)


@router.post("/{worktree_name}/archive", response_model=WorktreeResponse)
def archive_worktree(worktree_name: str, db_path: str = Depends(get_db_path)) -> WorktreeResponse:
    wt = storage.get_worktree_by_name(db_path, worktree_name)
    if wt is None:
        raise HTTPException(status_code=404, detail=f"worktree '{worktree_name}' not found")
    storage.update_worktree_status(db_path, wt.id, worktrees.WORKTREE_ARCHIVED)
    return _to_response(db_path, storage.get_worktree_by_id(db_path, wt.id))


@router.delete("/{worktree_name}")
def delete_worktree(
    worktree_name: str,
    force: bool = Query(default=False),
    db_path: str = Depends(get_db_path),
) -> Dict[str, object]:
    wt = storage.get_worktree_by_name(db_path, worktree_name)
    if wt is None:
        raise HTTPException(status_code=404, detail=f"worktree '{worktree_name}' not found")
    if not force and storage.count_active_runs_for_workspace(db_path, wt.path) > 0:
        raise HTTPException(
            status_code=400,
            detail=f"worktree '{worktree_name}' has an active run; pass ?force=true to remove anyway",
        )
    project = storage.get_project_by_id(db_path, wt.project_id)
    if project is None or not project.repo_path:
        raise HTTPException(status_code=400, detail=f"cannot resolve the repository for worktree '{worktree_name}'")
    try:
        worktrees.remove_git_worktree(project.repo_path, wt.path, force=force)
    except worktrees.WorktreeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    storage.delete_worktree_record(db_path, wt.id)
    return {"deleted": wt.name}
