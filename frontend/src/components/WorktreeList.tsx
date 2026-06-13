import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { Worktree } from "../types";
import { Section } from "./Layout";

export function WorktreeList({
  refreshKey,
  onChanged,
}: {
  refreshKey: number;
  onChanged: () => void;
}) {
  const [worktrees, setWorktrees] = useState<Worktree[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setWorktrees(await api.listWorktrees());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [refreshKey]);

  async function archive(name: string) {
    setBusy(true);
    setError(null);
    try {
      await api.archiveWorktree(name);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(name: string) {
    if (!window.confirm(`Remove worktree "${name}"? This runs "git worktree remove".`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteWorktree(name);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section
      title="Worktrees"
      actions={
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      }
    >
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && worktrees.length === 0 && (
        <p className="muted">No worktrees yet. Create one to run isolated parallel sessions.</p>
      )}
      {worktrees.length > 0 && (
        <table className="table worktrees">
          <thead>
            <tr>
              <th>Project</th>
              <th>Name</th>
              <th>Branch</th>
              <th>Status</th>
              <th>Path</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {worktrees.map((wt) => (
              <tr key={wt.id}>
                <td>{wt.project}</td>
                <td>{wt.name}</td>
                <td className="mono">{wt.branch}</td>
                <td>
                  <span className="status">{wt.status}</span>
                </td>
                <td className="mono">{wt.path}</td>
                <td>
                  <div className="row-actions">
                    <button
                      onClick={() => void archive(wt.name)}
                      disabled={busy || wt.status === "ARCHIVED"}
                    >
                      Archive
                    </button>
                    <button className="danger" onClick={() => void remove(wt.name)} disabled={busy}>
                      Remove
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}
