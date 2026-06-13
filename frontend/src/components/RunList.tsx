import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import { CANCELLABLE_RUN_STATUSES, type RunSummary } from "../types";
import { Section } from "./Layout";

function shorten(text: string, limit = 50): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length <= limit ? collapsed : collapsed.slice(0, limit - 1) + "…";
}

function statusClass(status: string): string {
  return "rs rs-" + status.toLowerCase().replace(/_/g, "-");
}

export function RunList({
  refreshKey,
  onSelect,
}: {
  refreshKey: number;
  onSelect: (id: number) => void;
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setRuns(await api.listRuns());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [refreshKey]);

  async function cancel(id: number) {
    if (!window.confirm(`Cancel run #${id}?`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.cancelRun(id);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section
      title="Runs"
      actions={
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      }
    >
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && runs.length === 0 && <p className="muted">No runs yet.</p>}
      {runs.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Status</th>
              <th>Queue</th>
              <th>Provider</th>
              <th>Created</th>
              <th>Prompt</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} className="clickable" onClick={() => onSelect(run.id)}>
                <td>{run.id}</td>
                <td>
                  <span className={statusClass(run.status)}>{run.status}</span>
                </td>
                <td>{run.queue_status ? <span className="status">{run.queue_status}</span> : "—"}</td>
                <td>{run.provider}</td>
                <td className="mono">{run.created_at}</td>
                <td>{shorten(run.prompt)}</td>
                <td>
                  {CANCELLABLE_RUN_STATUSES.includes(run.status) && (
                    <button
                      className="danger"
                      disabled={busy}
                      onClick={(e) => {
                        e.stopPropagation();
                        void cancel(run.id);
                      }}
                    >
                      Cancel
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}
