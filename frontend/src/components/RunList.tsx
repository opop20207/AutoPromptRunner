import { useEffect, useMemo, useState } from "react";

import { api, errorMessage } from "../api/client";
import { CANCELLABLE_RUN_STATUSES, type RunSummary } from "../types";
import { Section } from "./Layout";
import { StatusBadge } from "./StatusBadge";

const STATUS_OPTIONS = ["all", "CREATED", "RUNNING", "WAITING_APPROVAL", "DONE", "FAILED", "STOPPED"];

function shorten(text: string, limit = 60): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length <= limit ? collapsed : collapsed.slice(0, limit - 1) + "…";
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
  const [statusFilter, setStatusFilter] = useState("all");
  const [providerFilter, setProviderFilter] = useState("all");

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

  const providers = useMemo(
    () => ["all", ...Array.from(new Set(runs.map((r) => r.provider)))],
    [runs],
  );
  const filtered = runs.filter(
    (r) =>
      (statusFilter === "all" || r.status === statusFilter) &&
      (providerFilter === "all" || r.provider === providerFilter),
  );

  return (
    <Section
      title="Runs"
      actions={
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      }
    >
      <div className="filters">
        <label>
          Status
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          Provider
          <select value={providerFilter} onChange={(e) => setProviderFilter(e.target.value)}>
            {providers.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <span className="muted">{filtered.length} run(s)</span>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && runs.length === 0 && <p className="muted">Loading…</p>}
      {!loading && !error && runs.length === 0 && <p className="muted">No runs yet.</p>}
      {runs.length > 0 && filtered.length === 0 && <p className="muted">No runs match the filters.</p>}
      {filtered.length > 0 && (
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
            {filtered.map((run) => (
              <tr key={run.id} className="clickable" onClick={() => onSelect(run.id)}>
                <td>{run.id}</td>
                <td>
                  <StatusBadge status={run.status} />
                </td>
                <td>{run.queue_status ? <StatusBadge status={run.queue_status} /> : "—"}</td>
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
