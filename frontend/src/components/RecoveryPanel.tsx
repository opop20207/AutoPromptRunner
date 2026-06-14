import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { RecoveryAttempt } from "../types";
import { StatusBadge } from "./StatusBadge";

// Failure recovery for a FAILED run: propose a focused, rule-generated recovery prompt,
// approve / reject it, and execute it (creating a new linked run). Shown only when the run
// is FAILED or already has recovery attempts.
export function RecoveryPanel({
  runId,
  runStatus,
  refreshKey,
  onChanged,
  onOpenRun,
}: {
  runId: number;
  runStatus: string;
  refreshKey: number;
  onChanged: () => void;
  onOpenRun: (id: number) => void;
}) {
  const [recoveries, setRecoveries] = useState<RecoveryAttempt[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setRecoveries((await api.getRunRecoveries(runId)).recoveries);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [runId, refreshKey]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const isFailed = runStatus === "FAILED";
  // Render nothing unless the run failed or there is recovery history to show.
  if (!isFailed && recoveries.length === 0) {
    return null;
  }

  return (
    <div className="subsection">
      <h3>Recovery</h3>
      {isFailed && (
        <p className="muted">
          This run failed. Propose a focused recovery to fix only the failed step (a new linked run is created).
        </p>
      )}
      <div className="row-actions">
        <button onClick={() => void act(() => api.proposeRecovery(runId))} disabled={busy || !isFailed}>
          Propose recovery
        </button>
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && recoveries.length === 0 && <p className="muted">Loading…</p>}
      {!loading && recoveries.length === 0 && <p className="muted">No recovery attempts yet.</p>}

      {recoveries.map((rec) => {
        const decided = rec.status === "REJECTED" || rec.status === "EXECUTED";
        return (
          <div key={rec.id} className="recovery-item">
            <div className="recovery-head">
              <strong>Recovery #{rec.id}</strong>
              <StatusBadge status={rec.status} />
              {rec.recovery_run_id != null && (
                <button className="link-btn" onClick={() => onOpenRun(rec.recovery_run_id as number)}>
                  open run #{rec.recovery_run_id}
                </button>
              )}
            </div>
            <p className="preview-cell">
              {expanded === rec.id ? rec.recovery_prompt : rec.recovery_prompt.slice(0, 160) + (rec.recovery_prompt.length > 160 ? "…" : "")}
            </p>
            <div className="row-actions">
              <button onClick={() => setExpanded(expanded === rec.id ? null : rec.id)}>
                {expanded === rec.id ? "Hide prompt" : "Show full prompt"}
              </button>
              {rec.status === "PROPOSED" && (
                <button onClick={() => void act(() => api.approveRecovery(rec.id))} disabled={busy}>
                  Approve
                </button>
              )}
              {!decided && (
                <button onClick={() => void act(() => api.rejectRecovery(rec.id))} disabled={busy}>
                  Reject
                </button>
              )}
              {(rec.status === "PROPOSED" || rec.status === "APPROVED" || rec.status === "FAILED") && (
                <>
                  <button
                    className="primary"
                    onClick={() => void act(() => api.executeRecovery(rec.id, false))}
                    disabled={busy}
                  >
                    Execute
                  </button>
                  <button onClick={() => void act(() => api.executeRecovery(rec.id, true))} disabled={busy}>
                    Execute queued
                  </button>
                </>
              )}
            </div>
            {rec.reason && <p className="muted">Reason: {rec.reason}</p>}
          </div>
        );
      })}
    </div>
  );
}
