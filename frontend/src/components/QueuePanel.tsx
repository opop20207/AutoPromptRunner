import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { QueueJob, SystemStatus } from "../types";
import { StatusBadge } from "./StatusBadge";

// Shows the local run queue (queued / running / done / failed / cancelled jobs). QUEUED or
// RUNNING jobs can be cancelled (running cancellation is best-effort). When `runId` is
// given, the current run's row is highlighted. A system-status probe surfaces a warning when
// RUNNING jobs look orphaned (no live worker), suggesting reconciliation in the System panel.
export function QueuePanel({
  runId,
  refreshKey,
  onChanged,
  onOpenSystem,
}: {
  runId?: number;
  refreshKey?: number;
  onChanged?: () => void;
  onOpenSystem?: () => void;
}) {
  const [jobs, setJobs] = useState<QueueJob[]>([]);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [queueJobs, sys] = await Promise.all([
        api.listQueue(),
        api.getSystemStatus().catch(() => null), // status is advisory; never block the queue view
      ]);
      setJobs(queueJobs);
      setStatus(sys);
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
  // A RUNNING job with no live worker is likely orphaned by a crashed/stopped worker.
  const orphanedRunning = hasRunning && status != null && status.active_workers === 0;

  return (
    <div>
      <div className="row-actions" style={{ justifyContent: "flex-end", marginBottom: 8 }}>
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {loading && jobs.length === 0 && <p className="muted">Loading…</p>}
      {orphanedRunning && (
        <div className="warning-box">
          RUNNING queue job(s) but no live worker — they may be stale (worker interrupted).{" "}
          {onOpenSystem ? (
            <button className="link-btn" onClick={onOpenSystem}>
              Reconcile in the System panel
            </button>
          ) : (
            "Reconcile from the System panel."
          )}
        </div>
      )}
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
