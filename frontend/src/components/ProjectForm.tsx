import { type FormEvent, useState } from "react";

import { api, errorMessage } from "../api/client";
import { PROVIDERS } from "../types";
import { Section } from "./Layout";

export function ProjectForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [repoPath, setRepoPath] = useState("");
  const [provider, setProvider] = useState<string>("mock");
  const [maxLoops, setMaxLoops] = useState(5);
  const [requireApproval, setRequireApproval] = useState(true);
  const [timeoutSeconds, setTimeoutSeconds] = useState(1800);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createProject({
        name,
        repo_path: repoPath,
        default_provider: provider,
        default_max_loops: maxLoops,
        require_approval: requireApproval,
        timeout_seconds: timeoutSeconds,
      });
      setName("");
      setRepoPath("");
      onCreated();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="New Project">
      <form className="form" onSubmit={submit}>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label>
          Repo path
          <input value={repoPath} onChange={(e) => setRepoPath(e.target.value)} required />
        </label>
        <label>
          Default provider
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            {PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label>
          Default max loops
          <input
            type="number"
            min={1}
            value={maxLoops}
            onChange={(e) => setMaxLoops(Number(e.target.value))}
          />
        </label>
        <label>
          Timeout (seconds)
          <input
            type="number"
            min={1}
            value={timeoutSeconds}
            onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
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
        {error && <p className="error">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Creating…" : "Create project"}
        </button>
      </form>
    </Section>
  );
}
