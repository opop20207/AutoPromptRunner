// API response/request shapes, mirroring autoprompt_runner.api.schemas.

export const PROVIDERS = ["mock", "claude-code", "codex"] as const;

// Safety hard limits (mirrors autoprompt_runner.config), used for UI hints.
export const MAX_LOOPS_HARD_LIMIT = 20;
export const TIMEOUT_SECONDS_HARD_LIMIT = 7200;

export type RunStatus =
  | "CREATED"
  | "RUNNING"
  | "WAITING_APPROVAL"
  | "DONE"
  | "FAILED"
  | "STOPPED";

// Artifact type options for the ArtifactList filter ("all" means no filter).
export const ARTIFACT_TYPES = [
  "all",
  "git_status_before",
  "git_status_after",
  "git_diff",
  "git_diff_stat",
  "changed_files",
  "runner_stdout",
  "runner_stderr",
] as const;
export type ArtifactTypeFilter = (typeof ARTIFACT_TYPES)[number];

export interface Health {
  status: string;
  service: string;
}

export interface Project {
  id: number;
  name: string;
  repo_path: string | null;
  default_provider: string | null;
  default_max_loops: number | null;
  require_approval: boolean;
  timeout_seconds: number | null;
  created_at: string;
  updated_at: string | null;
  is_default: boolean;
}

export interface ProjectCreate {
  name: string;
  repo_path: string;
  default_provider: string;
  default_max_loops: number;
  require_approval: boolean;
  timeout_seconds: number;
}

export interface RunSummary {
  id: number;
  status: RunStatus;
  provider: string;
  created_at: string;
  prompt: string;
  next_prompt?: string | null;
  approval_id?: number | null;
  step_id?: number | null;
  exit_code?: number | null;
  message?: string | null;
  warnings?: string[];
  queue_status?: string | null;
  queue_job_id?: number | null;
}

export interface RunCreate {
  prompt?: string | null;
  template?: string | null;
  goal?: string | null;
  extra_context?: string | null;
  worktree?: string | null;
  project?: string | null;
  provider?: string | null;
  workspace?: string | null;
  max_loops?: number | null;
  require_approval: boolean;
  timeout_seconds?: number | null;
  show_next_prompt: boolean;
  queued?: boolean;
}

export interface Template {
  id: number;
  name: string;
  description?: string | null;
  body: string;
  tags: string[];
  created_at: string;
  updated_at?: string | null;
}

export interface TemplateCreate {
  name: string;
  body: string;
  description?: string;
  tags?: string[];
}

export interface TemplateRender {
  project_name?: string | null;
  workspace?: string | null;
  goal?: string | null;
  extra_context?: string | null;
}

// The placeholders a template body may reference (mirrors templates.SUPPORTED_PLACEHOLDERS).
export const TEMPLATE_PLACEHOLDERS = [
  "project_name",
  "workspace",
  "goal",
  "changed_files",
  "last_error",
  "extra_context",
] as const;

export type WorktreeStatus = "ACTIVE" | "LOCKED" | "ARCHIVED";

export interface Worktree {
  id: number;
  project_id: number;
  project?: string | null;
  name: string;
  branch: string;
  path: string;
  base_branch?: string | null;
  status: WorktreeStatus;
  created_at: string;
  updated_at: string;
}

export interface WorktreeCreate {
  project: string;
  name: string;
  branch: string;
  base_branch?: string | null;
}

export type LockStatus = "ACTIVE" | "RELEASED" | "EXPIRED";

export interface RunLock {
  id: number;
  workspace_path: string;
  run_id: number;
  status: LockStatus;
  owner?: string | null;
  created_at: string;
  updated_at: string;
  expires_at?: string | null;
}

export type CancellationStatus = "REQUESTED" | "COMPLETED" | "FAILED";

// Run statuses for which a run can still be cancelled (non-terminal).
export const CANCELLABLE_RUN_STATUSES: RunStatus[] = ["CREATED", "RUNNING", "WAITING_APPROVAL"];

export type QueueStatus = "QUEUED" | "RUNNING" | "DONE" | "FAILED" | "CANCELLED";

export interface QueueJob {
  id: number;
  run_id: number;
  status: QueueStatus;
  priority: number;
  attempts: number;
  max_attempts: number;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  last_error?: string | null;
}

export interface SearchRunResult {
  id: number;
  status: string;
  provider: string;
  created_at: string;
  prompt_preview: string;
}

export interface SearchStepResult {
  id: number;
  run_id: number;
  loop_index: number;
  status: string;
  exit_code?: number | null;
  match_field: string;
  match_preview: string;
}

export interface SearchArtifactResult {
  id: number;
  run_id: number;
  step_id?: number | null;
  type: string;
  created_at: string;
  match_field: string;
  match_preview: string;
}

export interface SearchAllResponse {
  runs: SearchRunResult[];
  steps: SearchStepResult[];
  artifacts: SearchArtifactResult[];
}

export interface RunComparisonMetadata {
  id: number;
  status: string;
  provider: string;
  created_at: string;
  root_prompt_preview: string;
  root_prompt?: string | null;
}

export interface StepComparisonSummary {
  step_count_a: number;
  step_count_b: number;
  exit_codes_a: (number | null)[];
  exit_codes_b: (number | null)[];
  failed_steps_a: number;
  failed_steps_b: number;
}

export interface ChangedFilesComparison {
  only_a: string[];
  only_b: string[];
  common: string[];
  warning?: string | null;
}

export interface ArtifactTypeCounts {
  counts: Record<string, number>;
}

export interface RunComparisonResponse {
  run_a: RunComparisonMetadata;
  run_b: RunComparisonMetadata;
  same_provider: boolean;
  same_status: boolean;
  steps: StepComparisonSummary;
  changed_files: ChangedFilesComparison;
  diff_stat_a: string;
  diff_stat_b: string;
  latest_next_prompt_a: string;
  latest_next_prompt_b: string;
  latest_next_prompt_full_a?: string | null;
  latest_next_prompt_full_b?: string | null;
  artifact_counts_by_type_a: ArtifactTypeCounts;
  artifact_counts_by_type_b: ArtifactTypeCounts;
  summary: string;
}

export interface ArtifactTypeCountSummary {
  counts: Record<string, number>;
}

export interface PromptChainNode {
  node_id: string;
  run_id: number;
  step_id: number;
  loop_index: number;
  prompt?: string | null;
  prompt_preview: string;
  next_prompt?: string | null;
  next_prompt_preview: string;
  status: string;
  exit_code?: number | null;
  provider: string;
  started_at?: string | null;
  finished_at?: string | null;
  approval_status?: string | null;
  artifact_counts_by_type: ArtifactTypeCountSummary;
  changed_files_preview: string[];
  stderr_preview: string;
  stdout_preview: string;
}

export interface PromptChainResponse {
  run_id: number;
  root_prompt: string;
  provider: string;
  run_status: string;
  step_count: number;
  approval_count: number;
  pending_approval: boolean;
  failed_step_count: number;
  total_artifact_count: number;
  chain_nodes: PromptChainNode[];
}

export interface Step {
  id: number;
  loop_index: number;
  status: string;
  prompt: string;
  exit_code: number | null;
  stdout: string | null;
  stderr: string | null;
  next_prompt: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Approval {
  id: number;
  run_id: number;
  step_id: number;
  next_prompt: string;
  status: string;
  created_at: string;
  decided_at: string | null;
}

export interface ArtifactSummary {
  id: number;
  run_id: number;
  step_id: number | null;
  type: string;
  created_at: string;
  preview: string;
}

export interface ArtifactDetail {
  id: number;
  run_id: number;
  step_id: number | null;
  type: string;
  content: string | null;
  path: string | null;
  created_at: string;
}

export interface RunLogs {
  run_id: number;
  status: RunStatus;
  generated_at: string;
  latest_step_id: number | null;
  stdout: string;
  stderr: string;
  stdout_artifact_id: number | null;
  stderr_artifact_id: number | null;
}

export interface RunDetail {
  id: number;
  status: RunStatus;
  provider: string;
  workspace: string | null;
  prompt: string;
  max_loops: number;
  require_approval: boolean;
  created_at: string;
  finished_at: string | null;
  steps: Step[];
  pending_approval: Approval | null;
  artifacts: ArtifactSummary[];
  queue_status?: string | null;
  queue_job_id?: number | null;
  cancellation_status?: string | null;
  cancellation_reason?: string | null;
}
