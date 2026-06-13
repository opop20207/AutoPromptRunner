// API response/request shapes, mirroring autoprompt_runner.api.schemas.

export const PROVIDERS = ["mock", "claude-code", "codex"] as const;

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
  status: string;
  provider: string;
  created_at: string;
  prompt: string;
  next_prompt?: string | null;
  approval_id?: number | null;
  step_id?: number | null;
  exit_code?: number | null;
  message?: string | null;
}

export interface RunCreate {
  prompt: string;
  project?: string | null;
  provider?: string | null;
  workspace?: string | null;
  max_loops?: number | null;
  require_approval: boolean;
  timeout_seconds?: number | null;
  show_next_prompt: boolean;
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

export interface RunDetail {
  id: number;
  status: string;
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
