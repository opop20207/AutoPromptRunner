import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { ReconciliationReport, SystemStatus, WorkerHeartbeat } from "../types";
import { StatusBadge } from "./StatusBadge";

// Crash / restart recovery panel. Shows a snapshot of workers, queue jobs, locks, and stale
// state, and lets the operator reconcile it. "Dry-run" reports what would change without
// touching anything; "Reconcile" applies it (non-destructive: only DB rows change, no files
// are deleted and no Git command is run).
export function SystemPanel({
  refreshKey,
  onChanged,
  onOpenRun,
}: {
  refreshKey?: number;
  onChanged?: () => void;
  onOpenRun?: (id: number) => void;
}) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [workers, setWorkers] = useState<WorkerHeartbeat[]>([]);
  const [report, setReport] = useState<ReconciliationReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [s, w] = await Promise.all([api.getSystemStatus(), api.listWorkers()]);
      setStatus(s);
      setWorkers(w);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [refreshKey]);

  async function reconcile(dryRun: boolean) {
    if (!dryRun && !window.confirm("Reconcile stale state now? Stale RUNNING runs are marked FAILED/STOPPED and expired locks released (no files are deleted).")) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.reconcileSystem(dryRun);
      setReport(result);
      if (!dryRun) {
        await load();
        onChanged?.();
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const warnings: string[] = [];
  if (status) {
    if (status.stale_runs > 0)
      warnings.push(`${status.stale_runs} stale RUNNING run(s) — likely left by an interrupted worker.`);
    if (status.stale_locks > 0)
      warnings.push(`${status.stale_locks} stale workspace lock(s) past expiry or held by a finished run.`);
    if (status.stale_workers > 0)
      warnings.push(`${status.stale_workers} worker heartbeat(s) with no recent update — the worker may have crashed.`);
    if (status.running_jobs > 0 && status.active_workers === 0)
      warnings.push(`${status.running_jobs} RUNNING queue job(s) but no live worker — these may be orphaned.`);
  }

  return (
    <div>
      <p className="muted">
        Recover after an API/worker crash, machine restart, or interrupted run. Reconciliation only
        updates database rows — it never deletes files or runs Git commands.
      </p>

      <div className="row-actions" style={{ marginBottom: 8 }}>
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
        <button onClick={() => void reconcile(true)} disabled={busy || loading}>
          Dry-run (preview)
        </button>
        <button className="primary" onClick={() => void reconcile(false)} disabled={busy || loading}>
          Reconcile
        </button>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && !status && <p className="muted">Loading…</p>}

      {warnings.length > 0 && (
        <div className="warning-box">
          <strong>Stale state detected</strong>
          <ul>
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {status && (
        <div className="subsection">
          <h3>Status</h3>
          <table className="table">
            <tbody>
              <tr>
                <th>Workers</th>
                <td>
                  {status.active_workers} active
                  {status.stale_workers > 0 ? `, ${status.stale_workers} stale` : ""}
                </td>
              </tr>
              <tr>
                <th>Queue</th>
                <td>
                  {status.queued_jobs} queued, {status.running_jobs} running
                </td>
              </tr>
              <tr>
                <th>Locks</th>
                <td>
                  {status.active_locks} active
                  {status.stale_locks > 0 ? `, ${status.stale_locks} stale` : ""}
                </td>
              </tr>
              <tr>
                <th>Runs</th>
                <td>{status.stale_runs} stale RUNNING</td>
              </tr>
            </tbody>
          </table>
          {!loading && warnings.length === 0 && <p className="muted">No stale state detected.</p>}
        </div>
      )}

      {report && (
        <div className="subsection">
          <h3>Last reconciliation {report.dry_run ? "(dry-run preview)" : "(applied)"}</h3>
          <p className="muted">
            {report.stale_runs} run(s), {report.stale_queue_jobs} job(s), {report.stale_locks} lock(s),{" "}
            {report.orphaned_cancellations} cancellation(s), {report.stale_workers} worker(s).
          </p>
          {report.actions.length === 0 ? (
            <p className="muted">Nothing to reconcile.</p>
          ) : (
            <div className="scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Kind</th>
                    <th>Target</th>
                    <th>Run</th>
                    <th>Action</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {report.actions.map((a, i) => (
                    <tr key={i}>
                      <td>{a.kind}</td>
                      <td>#{a.target_id}</td>
                      <td>
                        {a.run_id ? (
                          onOpenRun ? (
                            <button className="link-btn" onClick={() => onOpenRun(a.run_id as number)}>
                              #{a.run_id}
                            </button>
                          ) : (
                            `#${a.run_id}`
                          )
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>{a.action}</td>
                      <td className="muted">{a.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <div className="subsection">
        <h3>Workers</h3>
        {workers.length === 0 ? (
          <p className="muted">No worker heartbeats recorded.</p>
        ) : (
          <div className="scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Worker</th>
                  <th>Status</th>
                  <th>Started</th>
                  <th>Updated</th>
                  <th>Stopped</th>
                </tr>
              </thead>
              <tbody>
                {workers.map((w) => (
                  <tr key={w.id}>
                    <td className="mono">{w.worker_id}</td>
                    <td>
                      <StatusBadge status={w.status} />
                    </td>
                    <td className="mono">{w.started_at}</td>
                    <td className="mono">{w.updated_at}</td>
                    <td className="mono">{w.stopped_at ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
