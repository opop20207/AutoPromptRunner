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
    prompt: Optional[str] = None
    template: Optional[str] = None
    goal: Optional[str] = None
    extra_context: Optional[str] = None
    worktree: Optional[str] = None
    project: Optional[str] = None
    provider: Optional[str] = None
    workspace: Optional[str] = None
    max_loops: Optional[int] = None
    require_approval: bool = True
    timeout_seconds: Optional[int] = None
    show_next_prompt: bool = False
    queued: bool = True  # API default: create + enqueue for a background worker


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
    queue_status: Optional[str] = None
    queue_job_id: Optional[int] = None


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
    queue_status: Optional[str] = None
    queue_job_id: Optional[int] = None
    cancellation_status: Optional[str] = None
    cancellation_reason: Optional[str] = None


class ArtifactDetailResponse(BaseModel):
    id: int
    run_id: int
    step_id: Optional[int] = None
    type: str
    content: Optional[str] = None
    path: Optional[str] = None
    created_at: str


class TemplateCreateRequest(BaseModel):
    name: str
    body: str
    description: Optional[str] = ""
    tags: List[str] = []


class TemplateResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    body: str
    tags: List[str] = []
    created_at: str
    updated_at: Optional[str] = None


class TemplateRenderRequest(BaseModel):
    project_name: Optional[str] = None
    workspace: Optional[str] = None
    goal: Optional[str] = None
    changed_files: Optional[List[str]] = None
    last_error: Optional[str] = None
    extra_context: Optional[str] = None


class TemplateRenderResponse(BaseModel):
    name: str
    rendered: str


class TemplateSeedResponse(BaseModel):
    seeded: int
    skipped: int
    total: int


class WorktreeCreateRequest(BaseModel):
    project: str
    name: str
    branch: str
    base_branch: Optional[str] = None


class WorktreeResponse(BaseModel):
    id: int
    project_id: int
    project: Optional[str] = None
    name: str
    branch: str
    path: str
    base_branch: Optional[str] = None
    status: str
    created_at: str
    updated_at: str


class SearchRunResult(BaseModel):
    id: int
    status: str
    provider: str
    created_at: str
    prompt_preview: str


class SearchStepResult(BaseModel):
    id: int
    run_id: int
    loop_index: int
    status: str
    exit_code: Optional[int] = None
    match_field: str
    match_preview: str


class SearchArtifactResult(BaseModel):
    id: int
    run_id: int
    step_id: Optional[int] = None
    type: str
    created_at: str
    match_field: str
    match_preview: str


class SearchAllResponse(BaseModel):
    runs: List[SearchRunResult] = []
    steps: List[SearchStepResult] = []
    artifacts: List[SearchArtifactResult] = []
