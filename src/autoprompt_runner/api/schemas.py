"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str


class ProjectCreateRequest(BaseModel):
    name: str
    repo_path: str
    default_provider: str = "mock"
    default_max_loops: int = 1
    require_approval: bool = True
    timeout_seconds: int = 1800


class ProjectResponse(BaseModel):
    id: int
    name: str
    repo_path: Optional[str] = None
    default_provider: Optional[str] = None
    default_max_loops: Optional[int] = None
    require_approval: bool = True
    timeout_seconds: Optional[int] = None
    created_at: str
    updated_at: Optional[str] = None
    is_default: bool = False


class RunCreateRequest(BaseModel):
    prompt: str
    project: Optional[str] = None
    provider: Optional[str] = None
    workspace: Optional[str] = None
    max_loops: Optional[int] = None
    require_approval: bool = True
    timeout_seconds: Optional[int] = None
    show_next_prompt: bool = False


class RunSummaryResponse(BaseModel):
    id: int
    status: str
    provider: str
    created_at: str
    prompt: str
    next_prompt: Optional[str] = None
    approval_id: Optional[int] = None
    step_id: Optional[int] = None
    exit_code: Optional[int] = None
    message: Optional[str] = None
    warnings: List[str] = []


class StepResponse(BaseModel):
    id: int
    loop_index: int
    status: str
    prompt: str
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    next_prompt: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class ApprovalResponse(BaseModel):
    id: int
    run_id: int
    step_id: int
    next_prompt: str
    status: str
    created_at: str
    decided_at: Optional[str] = None


class ArtifactSummaryResponse(BaseModel):
    id: int
    run_id: int
    step_id: Optional[int] = None
    type: str
    created_at: str
    preview: str = ""


class RunDetailResponse(BaseModel):
    id: int
    status: str
    provider: str
    workspace: Optional[str] = None
    prompt: str
    max_loops: int
    require_approval: bool
    created_at: str
    finished_at: Optional[str] = None
    steps: List[StepResponse] = []
    pending_approval: Optional[ApprovalResponse] = None
    artifacts: List[ArtifactSummaryResponse] = []


class ArtifactDetailResponse(BaseModel):
    id: int
    run_id: int
    step_id: Optional[int] = None
    type: str
    content: Optional[str] = None
    path: Optional[str] = None
    created_at: str
