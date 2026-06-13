import { type FormEvent, useState } from "react";

import { api, errorMessage } from "../api/client";
import { Section } from "./Layout";

export function WorktreeForm({
  defaultProject,
  onCreated,
}: {
  defaultProject: string;
  onCreated: () => void;
}) {
  const [project, setProject] = useState(defaultProject);
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("");
  const [baseBranch, setBaseBranch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createWorktree({
        project,
        name,
        branch,
        base_branch: baseBranch.trim() || null,
      });
      setName("");
      setBranch("");
      setBaseBranch("");
      onCreated();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="New Worktree">
      <form className="form" onSubmit={submit}>
        <label>
          Project
          <input value={project} onChange={(e) => setProject(e.target.value)} required />
        </label>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="ui-session" required />
        </label>
        <label>
          Branch
          <input
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder="autoprompt/ui-session"
            required
          />
        </label>
        <label>
          Base branch (optional; defaults to current HEAD)
          <input value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)} placeholder="main" />
        </label>
        <p className="muted">
          Creates an isolated Git worktree (<span className="mono">git worktree add</span>) on a new branch, so
          parallel sessions never share one working tree.
        </p>
        {error && <p className="error">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Creating…" : "Create worktree"}
        </button>
      </form>
    </Section>
  );
}
