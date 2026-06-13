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
}

export interface RunCreate {
  prompt?: string | null;
  template?: string | null;
  goal?: string | null;
  extra_context?: string | null;
  project?: string | null;
  provider?: string | null;
  workspace?: string | null;
  max_loops?: number | null;
  require_approval: boolean;
  timeout_seconds?: number | null;
  show_next_prompt: boolean;
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
}
