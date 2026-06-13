import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { QueueJob } from "../types";
import { StatusBadge } from "./StatusBadge";

// Shows the local run queue (queued / running / done / failed / cancelled jobs). QUEUED or
// RUNNING jobs can be cancelled (running cancellation is best-effort). When `runId` is
// given, the current run's row is highlighted.
export function QueuePanel({
  runId,
  refreshKey,
  onChanged,
}: {
  runId?: number;
  refreshKey?: number;
  onChanged?: () => void;
}) {
  const [jobs, setJobs] = useState<QueueJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setJobs(await api.listQueue());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [refreshKey]);

  async function cancel(jobRunId: number) {
    if (!window.confirm(`Cancel run ${jobRunId}?`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.cancelRun(jobRunId); // uses the run cancellation service
      await load();
      onChanged?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const hasRunning = jobs.some((job) => job.status === "RUNNING");

  return (
    <div>
      <div className="row-actions" style={{ justifyContent: "flex-end", marginBottom: 8 }}>
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {loading && jobs.length === 0 && <p className="muted">Loading…</p>}
      {hasRunning && (
        <p className="muted">
          Cancelling a RUNNING job is best-effort — the worker process is force-stopped only if it is local.
        </p>
      )}
      {!loading && !error && jobs.length === 0 && <p className="muted">No queue jobs.</p>}
      {jobs.length > 0 && (
        <div className="scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Job</th>
                <th>Run</th>
                <th>Status</th>
                <th>Prio</th>
                <th>Attempts</th>
                <th>Created</th>
                <th>Finished</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id} className={runId && job.run_id === runId ? "selected" : undefined}>
                  <td>#{job.id}</td>
                  <td>#{job.run_id}</td>
                  <td>
                    <StatusBadge status={job.status} />
                  </td>
                  <td>{job.priority}</td>
                  <td>
                    {job.attempts}/{job.max_attempts}
                  </td>
                  <td className="mono">{job.created_at}</td>
                  <td className="mono">{job.finished_at ?? "—"}</td>
                  <td>
                    {(job.status === "QUEUED" || job.status === "RUNNING") && (
                      <button className="danger" disabled={busy} onClick={() => void cancel(job.run_id)}>
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
