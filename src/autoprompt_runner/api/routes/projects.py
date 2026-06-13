"""Project routes. Thin handlers over the existing storage layer."""

from __future__ import annotations

import os
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException

from ... import storage
from ...models import Project
from ...services.run_service import DEFAULT_PROVIDER_FACTORIES
from ..dependencies import get_db_path
from ..schemas import ProjectCreateRequest, ProjectResponse

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_response(project: Project, is_default: bool) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        repo_path=project.repo_path,
        default_provider=project.default_provider,
        default_max_loops=project.default_max_loops,
        require_approval=project.require_approval,
        timeout_seconds=project.timeout_seconds,
        created_at=project.created_at,
        updated_at=project.updated_at,
        is_default=is_default,
    )


@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(body: ProjectCreateRequest, db_path: str = Depends(get_db_path)) -> ProjectResponse:
    if body.default_provider not in DEFAULT_PROVIDER_FACTORIES:
        supported = ", ".join(sorted(DEFAULT_PROVIDER_FACTORIES))
        raise HTTPException(status_code=400, detail=f"unsupported provider '{body.default_provider}'. Supported: {supported}")
    if body.default_max_loops < 1:
        raise HTTPException(status_code=400, detail="default_max_loops must be >= 1")
    if body.timeout_seconds < 1:
        raise HTTPException(status_code=400, detail="timeout_seconds must be >= 1")
    if not os.path.isdir(body.repo_path):
        raise HTTPException(status_code=400, detail=f"repo_path does not exist or is not a directory: {body.repo_path}")
    if storage.get_project_by_name(db_path, body.name) is not None:
        raise HTTPException(status_code=400, detail=f"project '{body.name}' already exists")

    project_id = storage.create_project(
        db_path,
        name=body.name,
        repo_path=body.repo_path,
        default_provider=body.default_provider,
        default_max_loops=body.default_max_loops,
        require_approval=body.require_approval,
        timeout_seconds=body.timeout_seconds,
    )
    project = storage.get_project_by_id(db_path, project_id)
    return _to_response(project, is_default=False)


@router.get("", response_model=List[ProjectResponse])
def list_projects(db_path: str = Depends(get_db_path)) -> List[ProjectResponse]:
    default = storage.get_default_project(db_path)
    default_id = default.id if default is not None else None
    return [_to_response(project, is_default=(project.id == default_id)) for project in storage.list_projects(db_path)]


@router.get("/{project_name}", response_model=ProjectResponse)
def get_project(project_name: str, db_path: str = Depends(get_db_path)) -> ProjectResponse:
    project = storage.get_project_by_name(db_path, project_name)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project '{project_name}' not found")
    default = storage.get_default_project(db_path)
    return _to_response(project, is_default=(default is not None and default.id == project.id))


@router.post("/{project_name}/default")
def set_default_project(project_name: str, db_path: str = Depends(get_db_path)) -> Dict[str, object]:
    project = storage.get_project_by_name(db_path, project_name)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project '{project_name}' not found")
    storage.set_default_project(db_path, project.id)
    return {"default_project": project.name, "default_project_id": project.id}


@router.delete("/{project_name}")
def delete_project(project_name: str, db_path: str = Depends(get_db_path)) -> Dict[str, object]:
    project = storage.get_project_by_name(db_path, project_name)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project '{project_name}' not found")
    storage.delete_project(db_path, project.id)
    return {"deleted": project.name, "files_deleted": False}
