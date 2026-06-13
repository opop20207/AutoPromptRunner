import { type FormEvent, useState } from "react";

import { api, errorMessage } from "../api/client";
import { MAX_LOOPS_HARD_LIMIT, PROVIDERS, TIMEOUT_SECONDS_HARD_LIMIT } from "../types";
import { Section } from "./Layout";

export function RunForm({
  project,
  onProjectChange,
  onCreated,
}: {
  project: string;
  onProjectChange: (name: string) => void;
  onCreated: (runId: number) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [provider, setProvider] = useState<string>(""); // empty -> use project/default
  const [workspace, setWorkspace] = useState("");
  const [maxLoops, setMaxLoops] = useState("");
  const [requireApproval, setRequireApproval] = useState(true);
  const [timeoutSeconds, setTimeoutSeconds] = useState("");
  const [showNextPrompt, setShowNextPrompt] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.createRun({
        prompt,
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
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="New Run">
      <form className="form" onSubmit={submit}>
        <label>
          Prompt
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} required />
        </label>
        <label>
          Project (optional; blank uses the default project)
          <input value={project} onChange={(e) => onProjectChange(e.target.value)} />
        </label>
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
          Workspace override (required for claude-code / codex unless from a project)
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
