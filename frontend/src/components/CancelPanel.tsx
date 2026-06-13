import { useState } from "react";

import { api, errorMessage } from "../api/client";
import { CANCELLABLE_RUN_STATUSES, type RunDetail } from "../types";

// Cancels a run (queued / running / waiting) with an optional reason and a confirmation,
// then reloads the run detail and list. Shows the current cancellation status if present.
export function CancelPanel({ run, onCancelled }: { run: RunDetail; onCancelled: () => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cancellable = CANCELLABLE_RUN_STATUSES.includes(run.status);

  async function cancel() {
    if (!window.confirm(`Cancel run #${run.id}? This stops it and releases any workspace lock.`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.cancelRun(run.id, reason.trim() || undefined);
      setReason("");
      onCancelled();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {run.cancellation_status && (
        <p className="muted">
          Cancellation: <span className="status">{run.cancellation_status}</span>
          {run.cancellation_reason ? ` — ${run.cancellation_reason}` : ""}
        </p>
      )}
      {cancellable ? (
        <div className="form">
          <label>
            Reason (optional)
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="User stopped from web UI"
            />
          </label>
          <div className="row-actions">
            <button className="danger" onClick={() => void cancel()} disabled={busy}>
              {busy ? "Cancelling…" : "Cancel run"}
            </button>
          </div>
          <p className="muted">
            Cancelling a running job is best-effort: the external process is force-stopped only if it is
            running on this machine's worker.
          </p>
        </div>
      ) : (
        <p className="muted">This run is {run.status.toLowerCase()} and cannot be cancelled.</p>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
