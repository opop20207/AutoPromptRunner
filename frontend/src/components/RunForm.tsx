import { type FormEvent, useEffect, useState } from "react";

import { ApiError, api, errorMessage } from "../api/client";
import {
  MAX_LOOPS_HARD_LIMIT,
  PROVIDERS,
  TIMEOUT_SECONDS_HARD_LIMIT,
  type Template,
  type Worktree,
} from "../types";
import { Section } from "./Layout";

export function RunForm({
  project,
  template,
  worktree,
  templateRefresh,
  worktreeRefresh,
  onProjectChange,
  onTemplateChange,
  onWorktreeChange,
  onCreated,
}: {
  project: string;
  template: string;
  worktree: string;
  templateRefresh: number;
  worktreeRefresh: number;
  onProjectChange: (name: string) => void;
  onTemplateChange: (name: string) => void;
  onWorktreeChange: (name: string) => void;
  onCreated: (runId: number) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [goal, setGoal] = useState("");
  const [extraContext, setExtraContext] = useState("");
  const [provider, setProvider] = useState<string>(""); // empty -> use project/default
  const [workspace, setWorkspace] = useState("");
  const [maxLoops, setMaxLoops] = useState("");
  const [requireApproval, setRequireApproval] = useState(true);
  const [timeoutSeconds, setTimeoutSeconds] = useState("");
  const [showNextPrompt, setShowNextPrompt] = useState(false);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [worktrees, setWorktrees] = useState<Worktree[]>([]);
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const usingTemplate = template.trim() !== "";
  const selectedWorktree = worktrees.find((w) => w.name === worktree) ?? null;
  // Resolved workspace precedence: explicit workspace > worktree path > project repo_path.
  const resolvedWorkspace = workspace.trim() || selectedWorktree?.path || "(project default)";

  useEffect(() => {
    let cancelled = false;
    api
      .listTemplates()
      .then((items) => {
        if (!cancelled) setTemplates(items);
      })
      .catch(() => {
        /* the template selector is optional; ignore load errors here */
      });
    return () => {
      cancelled = true;
    };
  }, [templateRefresh]);

  useEffect(() => {
    let cancelled = false;
    api
      .listWorktrees()
      .then((items) => {
        if (!cancelled) setWorktrees(items);
      })
      .catch(() => {
        /* the worktree selector is optional; ignore load errors here */
      });
    return () => {
      cancelled = true;
    };
  }, [worktreeRefresh]);

  // Drop a stale preview whenever an input that feeds it changes.
  useEffect(() => {
    setPreview(null);
  }, [template, goal, extraContext, project, workspace]);

  async function previewTemplate() {
    if (!usingTemplate) return;
    setError(null);
    try {
      const result = await api.renderTemplate(template, {
        project_name: project.trim() || null,
        workspace: workspace.trim() || selectedWorktree?.path || null,
        goal: goal.trim() || null,
        extra_context: extraContext.trim() || null,
      });
      setPreview(result.rendered);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.createRun({
        prompt: usingTemplate ? null : prompt,
        template: usingTemplate ? template : null,
        goal: goal.trim() || null,
        extra_context: extraContext.trim() || null,
        worktree: worktree.trim() || null,
        project: project.trim() || null,
        provider: provider || null,
        workspace: workspace.trim() || null,
        max_loops: maxLoops ? Number(maxLoops) : null,
        require_approval: requireApproval,
        timeout_seconds: timeoutSeconds ? Number(timeoutSeconds) : null,
        show_next_prompt: showNextPrompt,
      });
      setPrompt("");
      onCreated(created.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(`Workspace locked — another active run holds it. ${err.message}`);
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="New Run">
      <form className="form" onSubmit={submit}>
        <label>
          Template (optional; overrides the direct prompt)
          <select value={template} onChange={(e) => onTemplateChange(e.target.value)}>
            <option value="">(direct prompt — no template)</option>
            {templates.map((t) => (
              <option key={t.id} value={t.name}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Prompt {usingTemplate ? "(disabled while a template is selected)" : ""}
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            required={!usingTemplate}
            disabled={usingTemplate}
          />
        </label>
        <label>
          Goal (fills {"{{goal}}"} in the template)
          <input value={goal} onChange={(e) => setGoal(e.target.value)} />
        </label>
        <label>
          Extra context (fills {"{{extra_context}}"})
          <input value={extraContext} onChange={(e) => setExtraContext(e.target.value)} />
        </label>
        {usingTemplate && (
          <div className="row-actions">
            <button type="button" onClick={() => void previewTemplate()}>
              Preview rendered prompt
            </button>
          </div>
        )}
        {usingTemplate && preview !== null && <pre className="block">{preview || "(empty)"}</pre>}
        <label>
          Project (optional; blank uses the default project)
          <input value={project} onChange={(e) => onProjectChange(e.target.value)} />
        </label>
        <label>
          Worktree (optional; runs in the worktree's isolated path)
          <select value={worktree} onChange={(e) => onWorktreeChange(e.target.value)}>
            <option value="">(no worktree)</option>
            {worktrees.map((w) => (
              <option key={w.id} value={w.name}>
                {w.name} {w.status !== "ACTIVE" ? `(${w.status})` : ""}
              </option>
            ))}
          </select>
        </label>
        {worktree.trim() !== "" && (
          <p className="muted">
            Resolved workspace: <span className="mono">{resolvedWorkspace}</span>
          </p>
        )}
        <label>
          Provider override
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="">(use project / default)</option>
            {PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label>
          Workspace override (highest precedence; required for claude-code / codex unless from a project)
          <input value={workspace} onChange={(e) => setWorkspace(e.target.value)} />
        </label>
        <label>
          Max loops (blank uses project/default; hard limit {MAX_LOOPS_HARD_LIMIT})
          <input
            type="number"
            min={1}
            max={MAX_LOOPS_HARD_LIMIT}
            value={maxLoops}
            onChange={(e) => setMaxLoops(e.target.value)}
          />
        </label>
        <label>
          Timeout seconds (blank uses project/default; hard limit {TIMEOUT_SECONDS_HARD_LIMIT})
          <input
            type="number"
            min={1}
            max={TIMEOUT_SECONDS_HARD_LIMIT}
            value={timeoutSeconds}
            onChange={(e) => setTimeoutSeconds(e.target.value)}
          />
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={requireApproval}
            onChange={(e) => setRequireApproval(e.target.checked)}
          />
          Require approval
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={showNextPrompt}
            onChange={(e) => setShowNextPrompt(e.target.checked)}
          />
          Show full next prompt
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Starting…" : "Start run"}
        </button>
      </form>
    </Section>
  );
}
