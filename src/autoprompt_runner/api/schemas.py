"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from typing import Dict, List, Optional

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


class RunComparisonMetadata(BaseModel):
    id: int
    status: str
    provider: str
    created_at: str
    root_prompt_preview: str
    root_prompt: Optional[str] = None


class StepComparisonSummary(BaseModel):
    step_count_a: int
    step_count_b: int
    exit_codes_a: List[Optional[int]] = []
    exit_codes_b: List[Optional[int]] = []
    failed_steps_a: int
    failed_steps_b: int


class ChangedFilesComparison(BaseModel):
    only_a: List[str] = []
    only_b: List[str] = []
    common: List[str] = []
    warning: Optional[str] = None


class ArtifactTypeCounts(BaseModel):
    counts: Dict[str, int] = {}


class RunComparisonResponse(BaseModel):
    run_a: RunComparisonMetadata
    run_b: RunComparisonMetadata
    same_provider: bool
    same_status: bool
    steps: StepComparisonSummary
    changed_files: ChangedFilesComparison
    diff_stat_a: str
    diff_stat_b: str
    latest_next_prompt_a: str
    latest_next_prompt_b: str
    latest_next_prompt_full_a: Optional[str] = None
    latest_next_prompt_full_b: Optional[str] = None
    artifact_counts_by_type_a: ArtifactTypeCounts
    artifact_counts_by_type_b: ArtifactTypeCounts
    summary: str


class ArtifactTypeCountSummary(BaseModel):
    counts: Dict[str, int] = {}


class PromptChainNode(BaseModel):
    node_id: str
    run_id: int
    step_id: int
    loop_index: int
    prompt: Optional[str] = None
    prompt_preview: str
    next_prompt: Optional[str] = None
    next_prompt_preview: str
    status: str
    exit_code: Optional[int] = None
    provider: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    approval_status: Optional[str] = None
    artifact_counts_by_type: ArtifactTypeCountSummary
    changed_files_preview: List[str] = []
    stderr_preview: str = ""
    stdout_preview: str = ""


class PromptChainResponse(BaseModel):
    run_id: int
    root_prompt: str
    provider: str
    run_status: str
    step_count: int
    approval_count: int
    pending_approval: bool
    failed_step_count: int
    total_artifact_count: int
    chain_nodes: List[PromptChainNode] = []


class ProviderProfileCreateRequest(BaseModel):
    name: str
    type: str
    command: str
    default_timeout_seconds: int = 1800
    default_args: Optional[str] = None
    enabled: bool = True


class ProviderProfileUpdateRequest(BaseModel):
    type: Optional[str] = None
    command: Optional[str] = None
    default_timeout_seconds: Optional[int] = None
    default_args: Optional[str] = None
    enabled: Optional[bool] = None


class ProviderProfileResponse(BaseModel):
    id: int
    name: str
    type: str
    command: str
    default_timeout_seconds: int
    default_args: Optional[str] = None
    enabled: bool
    available: bool
    created_at: str
    updated_at: str


class ProviderAvailabilityResponse(BaseModel):
    name: str
    type: str
    command: str
    available: bool


class RecoveryAttemptResponse(BaseModel):
    id: int
    source_run_id: int
    recovery_run_id: Optional[int] = None
    failed_step_id: Optional[int] = None
    status: str
    recovery_prompt: str
    reason: Optional[str] = None
    created_at: str
    decided_at: Optional[str] = None
    executed_at: Optional[str] = None


class RecoveryProposeRequest(BaseModel):
    reason: Optional[str] = None


class RecoveryDecisionRequest(BaseModel):
    reason: Optional[str] = None


class RecoveryExecuteRequest(BaseModel):
    queued: bool = False


class RecoveryListResponse(BaseModel):
    recoveries: List[RecoveryAttemptResponse] = []
