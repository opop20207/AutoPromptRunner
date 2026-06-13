// Thin fetch wrapper around the AutoPromptRunner HTTP API.
//
// The base URL defaults to http://localhost:8000 and can be overridden at build/dev
// time with the VITE_API_BASE_URL environment variable.

import type {
  ArtifactDetail,
  ArtifactSummary,
  Health,
  Project,
  ProjectCreate,
  RunCreate,
  RunDetail,
  RunLogs,
  RunSummary,
  RunLock,
  Template,
  TemplateCreate,
  TemplateRender,
  Worktree,
  WorktreeCreate,
} from "../types";

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
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
};

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return "Unexpected error";
}
