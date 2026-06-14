// Thin fetch wrapper around the AutoPromptRunner HTTP API.
//
// The base URL defaults to http://localhost:8000 and can be overridden at build/dev
// time with the VITE_API_BASE_URL environment variable.

import type {
  AppTarget,
  AppTargetCreate,
  ArtifactDetail,
  ArtifactSummary,
  Health,
  InjectOutcome,
  PromptQueue,
  QueuedPrompt,
  QueueSummary,
  Project,
  ProjectCreate,
  RunCreate,
  RunDetail,
  RunLogs,
  RunSummary,
  PromptChainResponse,
  ProviderAvailability,
  ProviderProfile,
  ProviderProfileCreate,
  ProviderProfileUpdate,
  ExportOptions,
  ExportPayload,
  ExportSummary,
  ImportSummary,
  CommitResult,
  CommitReview,
  QueueJob,
  RecoveryAttempt,
  RecoveryListResponse,
  ReconciliationReport,
  RollbackPlan,
  RollbackResult,
  RunCheckpoint,
  RunCommit,
  RunComparisonResponse,
  RunLock,
  SearchAllResponse,
  SearchArtifactResult,
  SearchRunResult,
  SystemStatus,
  Template,
  TemplateCreate,
  TemplateRender,
  WorkerHeartbeat,
  Worktree,
  WorktreeCreate,
} from "../types";

import { API_TOKEN_STORAGE_KEY } from "../types";

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// Optional API token (local-first auth). Stored only in the browser's localStorage and
// attached as `Authorization: Bearer <token>` to API requests below. Never logged.
export function getToken(): string {
  try {
    return localStorage.getItem(API_TOKEN_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(API_TOKEN_STORAGE_KEY, token.trim());
  } catch {
    // localStorage unavailable (e.g. private mode); ignore.
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(API_TOKEN_STORAGE_KEY);
  } catch {
    // ignore
  }
}

export function hasToken(): boolean {
  return getToken().length > 0;
}

function queryString(params: Record<string, string | number | boolean | undefined | null>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") qs.set(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  // Merge headers: content type, the optional bearer token, then any caller headers. The
  // token is only ever sent to BASE_URL (the API) -- every request goes through here.
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...((options?.headers as Record<string, string>) ?? {}),
  };
  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  } catch {
    throw new ApiError(0, `Cannot reach the backend at ${BASE_URL}. Is the API running?`);
  }

  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body && body.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      // Non-JSON error body; keep the status-line detail.
    }
    if (resp.status === 401) {
      detail = "Unauthorized (401) — enter a valid API token in the Auth control, or disable auth on the backend.";
    }
    throw new ApiError(resp.status, detail);
  }

  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

export const api = {
  base: BASE_URL,
  health: () => request<Health>("/health"),
  listProjects: () => request<Project[]>("/projects"),
  createProject: (body: ProjectCreate) =>
    request<Project>("/projects", { method: "POST", body: JSON.stringify(body) }),
  setDefaultProject: (name: string) =>
    request<unknown>(`/projects/${encodeURIComponent(name)}/default`, { method: "POST" }),
  deleteProject: (name: string) =>
    request<unknown>(`/projects/${encodeURIComponent(name)}`, { method: "DELETE" }),
  listRuns: () => request<RunSummary[]>("/runs"),
  createRun: (body: RunCreate) =>
    request<RunSummary>("/runs", { method: "POST", body: JSON.stringify(body) }),
  getRun: (id: number) => request<RunDetail>(`/runs/${id}`),
  getRunArtifacts: (runId: number, type?: string) => {
    const query = type && type !== "all" ? `?type=${encodeURIComponent(type)}` : "";
    return request<ArtifactSummary[]>(`/runs/${runId}/artifacts${query}`);
  },
  getArtifact: (artifactId: number) => request<ArtifactDetail>(`/artifacts/${artifactId}`),
  getRunLogs: (runId: number) => request<RunLogs>(`/runs/${runId}/logs`),
  approveNext: (id: number) =>
    request<RunSummary>(`/runs/${id}/approve-next`, { method: "POST" }),
  rejectNext: (id: number) =>
    request<RunSummary>(`/runs/${id}/reject-next`, { method: "POST" }),
  listTemplates: () => request<Template[]>("/templates"),
  createTemplate: (body: TemplateCreate) =>
    request<Template>("/templates", { method: "POST", body: JSON.stringify(body) }),
  getTemplate: (name: string) => request<Template>(`/templates/${encodeURIComponent(name)}`),
  deleteTemplate: (name: string) =>
    request<unknown>(`/templates/${encodeURIComponent(name)}`, { method: "DELETE" }),
  seedTemplates: () =>
    request<{ seeded: number; skipped: number; total: number }>("/templates/seed", { method: "POST" }),
  renderTemplate: (name: string, body: TemplateRender) =>
    request<{ name: string; rendered: string }>(`/templates/${encodeURIComponent(name)}/render`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listWorktrees: (project?: string) => {
    const query = project ? `?project=${encodeURIComponent(project)}` : "";
    return request<Worktree[]>(`/worktrees${query}`);
  },
  createWorktree: (body: WorktreeCreate) =>
    request<Worktree>("/worktrees", { method: "POST", body: JSON.stringify(body) }),
  getWorktree: (name: string) => request<Worktree>(`/worktrees/${encodeURIComponent(name)}`),
  archiveWorktree: (name: string) =>
    request<Worktree>(`/worktrees/${encodeURIComponent(name)}/archive`, { method: "POST" }),
  deleteWorktree: (name: string, force = false) =>
    request<unknown>(`/worktrees/${encodeURIComponent(name)}${force ? "?force=true" : ""}`, {
      method: "DELETE",
    }),
  listLocks: () => request<RunLock[]>("/locks"),
  releaseLock: (runId: number) =>
    request<{ run_id: number; released: number }>(`/locks/${runId}/release`, { method: "POST" }),
  listQueue: () => request<QueueJob[]>("/queue"),
  cancelQueueJob: (runId: number) =>
    request<{ run_id: number; cancelled: boolean }>(`/queue/${runId}/cancel`, { method: "POST" }),
  cancelRun: (runId: number, reason?: string) =>
    request<{
      run_id: number;
      run_status: string;
      cancelled: boolean;
      terminated: boolean;
      reason: string | null;
      message: string;
    }>(`/runs/${runId}/cancel`, { method: "POST", body: JSON.stringify({ reason: reason ?? null }) }),
  searchRuns: (params: { q?: string; status?: string; provider?: string; limit?: number; offset?: number }) =>
    request<SearchRunResult[]>(`/search/runs${queryString(params)}`),
  searchArtifacts: (params: { q?: string; type?: string; limit?: number; offset?: number }) =>
    request<SearchArtifactResult[]>(`/search/artifacts${queryString(params)}`),
  searchAll: (params: { q?: string; limit?: number; offset?: number }) =>
    request<SearchAllResponse>(`/search/all${queryString(params)}`),
  compareRuns: (params: {
    run_a: number;
    run_b: number;
    show_prompts?: boolean;
    show_artifacts?: boolean;
  }) => request<RunComparisonResponse>(`/compare/runs${queryString(params)}`),
  getRunChain: (
    runId: number,
    params: { full_prompts?: boolean; include_artifacts?: boolean; errors_only?: boolean } = {},
  ) => request<PromptChainResponse>(`/chains/runs/${runId}${queryString(params)}`),
  listProviders: () => request<ProviderProfile[]>("/providers"),
  seedProviders: () =>
    request<{ seeded: number; skipped: number; total: number }>("/providers/seed", { method: "POST" }),
  createProvider: (body: ProviderProfileCreate) =>
    request<ProviderProfile>("/providers", { method: "POST", body: JSON.stringify(body) }),
  getProvider: (name: string) => request<ProviderProfile>(`/providers/${encodeURIComponent(name)}`),
  updateProvider: (name: string, body: ProviderProfileUpdate) =>
    request<ProviderProfile>(`/providers/${encodeURIComponent(name)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  enableProvider: (name: string) =>
    request<ProviderProfile>(`/providers/${encodeURIComponent(name)}/enable`, { method: "POST" }),
  disableProvider: (name: string) =>
    request<ProviderProfile>(`/providers/${encodeURIComponent(name)}/disable`, { method: "POST" }),
  deleteProvider: (name: string) =>
    request<{ deleted: string }>(`/providers/${encodeURIComponent(name)}`, { method: "DELETE" }),
  checkProvider: (name: string) =>
    request<ProviderAvailability>(`/providers/${encodeURIComponent(name)}/check`),
  getRunRecoveries: (runId: number) =>
    request<RecoveryListResponse>(`/recovery/runs/${runId}`),
  proposeRecovery: (runId: number, reason?: string) =>
    request<RecoveryAttempt>(`/recovery/runs/${runId}/propose`, {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? null }),
    }),
  approveRecovery: (recoveryId: number) =>
    request<RecoveryAttempt>(`/recovery/${recoveryId}/approve`, { method: "POST" }),
  rejectRecovery: (recoveryId: number, reason?: string) =>
    request<RecoveryAttempt>(`/recovery/${recoveryId}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? null }),
    }),
  executeRecovery: (recoveryId: number, queued: boolean) =>
    request<RecoveryAttempt>(`/recovery/${recoveryId}/execute`, {
      method: "POST",
      body: JSON.stringify({ queued }),
    }),
  exportData: (options: Partial<ExportOptions>) =>
    request<ExportPayload>("/export-import/export", { method: "POST", body: JSON.stringify(options) }),
  importData: (payload: unknown, mode: string) =>
    request<ImportSummary>("/export-import/import", {
      method: "POST",
      body: JSON.stringify({ payload, mode }),
    }),
  summarizeExport: (payload: unknown) =>
    request<ExportSummary>("/export-import/summary", { method: "POST", body: JSON.stringify({ payload }) }),
  getSystemStatus: () => request<SystemStatus>("/system/status"),
  reconcileSystem: (dryRun: boolean) =>
    request<ReconciliationReport>("/system/reconcile", {
      method: "POST",
      body: JSON.stringify({ dry_run: dryRun }),
    }),
  listWorkers: () => request<WorkerHeartbeat[]>("/system/workers"),
  listCheckpoints: (runId: number) => request<RunCheckpoint[]>(`/checkpoints/runs/${runId}`),
  getCheckpoint: (id: number) => request<RunCheckpoint>(`/checkpoints/${id}`),
  getRollbackPlan: (id: number) => request<RollbackPlan>(`/checkpoints/${id}/rollback-plan`),
  rollbackCheckpoint: (id: number, confirm: boolean, force: boolean) =>
    request<RollbackResult>(`/checkpoints/${id}/rollback`, {
      method: "POST",
      body: JSON.stringify({ confirm, force }),
    }),
  getCommitReview: (runId: number, allowFailed = false) =>
    request<CommitReview>(`/commits/runs/${runId}/review${allowFailed ? "?allow_failed=true" : ""}`),
  proposeCommit: (runId: number, allowFailed = false) =>
    request<RunCommit>(`/commits/runs/${runId}/propose`, {
      method: "POST",
      body: JSON.stringify({ allow_failed: allowFailed }),
    }),
  applyCommit: (
    runId: number,
    body: { confirm: boolean; message?: string | null; files?: string[]; allow_failed?: boolean },
  ) => request<CommitResult>(`/commits/runs/${runId}/apply`, { method: "POST", body: JSON.stringify(body) }),
  listCommits: (runId: number) => request<RunCommit[]>(`/commits/runs/${runId}`),

  // -- Claude Code app prompt queue controller --
  listAppTargets: () => request<AppTarget[]>("/app-targets"),
  createAppTarget: (body: AppTargetCreate) =>
    request<AppTarget>("/app-targets", { method: "POST", body: JSON.stringify(body) }),
  getAppTarget: (id: number) => request<AppTarget>(`/app-targets/${id}`),
  updateAppTarget: (id: number, body: Partial<AppTargetCreate>) =>
    request<AppTarget>(`/app-targets/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  enableAppTarget: (id: number) => request<AppTarget>(`/app-targets/${id}/enable`, { method: "POST" }),
  disableAppTarget: (id: number) => request<AppTarget>(`/app-targets/${id}/disable`, { method: "POST" }),
  deleteAppTarget: (id: number) => request<{ deleted: number }>(`/app-targets/${id}`, { method: "DELETE" }),

  listPromptQueues: () => request<PromptQueue[]>("/prompt-queues"),
  createPromptQueue: (body: { name: string; app_target_id?: number | null; description?: string | null }) =>
    request<PromptQueue>("/prompt-queues", { method: "POST", body: JSON.stringify(body) }),
  getPromptQueue: (id: number) => request<QueueSummary>(`/prompt-queues/${id}`),
  deletePromptQueue: (id: number) =>
    request<{ deleted: number }>(`/prompt-queues/${id}`, { method: "DELETE" }),
  addQueuedPrompt: (queueId: number, body: { prompt: string; title?: string | null; position?: number | null }) =>
    request<QueuedPrompt>(`/prompt-queues/${queueId}/prompts`, { method: "POST", body: JSON.stringify(body) }),
  updateQueuedPrompt: (promptId: number, body: { title?: string | null; prompt?: string | null }) =>
    request<QueuedPrompt>(`/prompt-queues/prompts/${promptId}`, { method: "PATCH", body: JSON.stringify(body) }),
  reorderQueuedPrompt: (promptId: number, newPosition: number) =>
    request<QueuedPrompt>(`/prompt-queues/prompts/${promptId}/reorder`, {
      method: "POST",
      body: JSON.stringify({ new_position: newPosition }),
    }),
  injectCurrentPrompt: (queueId: number, restoreClipboard = false) =>
    request<InjectOutcome>(`/prompt-queues/${queueId}/inject-current`, {
      method: "POST",
      body: JSON.stringify({ restore_clipboard: restoreClipboard }),
    }),
  completeCurrentPrompt: (queueId: number) =>
    request<QueueSummary>(`/prompt-queues/${queueId}/complete-current`, { method: "POST" }),
  skipCurrentPrompt: (queueId: number) =>
    request<QueueSummary>(`/prompt-queues/${queueId}/skip-current`, { method: "POST" }),
  pausePromptQueue: (queueId: number) =>
    request<QueueSummary>(`/prompt-queues/${queueId}/pause`, { method: "POST" }),
  resumePromptQueue: (queueId: number) =>
    request<QueueSummary>(`/prompt-queues/${queueId}/resume`, { method: "POST" }),
  cancelPromptQueue: (queueId: number) =>
    request<QueueSummary>(`/prompt-queues/${queueId}/cancel`, { method: "POST" }),
};

// Build the SSE live-stream URL for a run. The API token (when stored) is appended as a
// query parameter because EventSource cannot set an Authorization header -- a local-only
// tradeoff (the token is never logged). Used only for the events stream endpoint.
export function eventStreamUrl(runId: number, afterId?: number): string {
  const token = getToken();
  const params: Record<string, string | number | undefined> = {};
  if (afterId != null) params.after_id = afterId;
  if (token) params.token = token;
  return `${BASE_URL}/events/runs/${runId}/stream${queryString(params)}`;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return "Unexpected error";
}
