import { useState } from "react";

import { api, errorMessage } from "../api/client";
import type { Approval } from "../types";

function preview(text: string, limit = 200): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length <= limit ? collapsed : collapsed.slice(0, limit - 1) + "…";
}

export function ApprovalPanel({
  runId,
  approval,
  onResolved,
}: {
  runId: number;
  approval: Approval;
  onResolved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showFull, setShowFull] = useState(false);

  async function act(action: (id: number) => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action(runId);
      // Parent reloads run detail, run list, and artifacts.
      onResolved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="approval">
      <div className="approval-head">
        <strong>Pending approval — next prompt</strong>
        <label className="checkbox inline">
          <input
            type="checkbox"
            checked={showFull}
            onChange={(e) => setShowFull(e.target.checked)}
          />
          Show full next prompt
        </label>
      </div>
      <pre className="block">{showFull ? approval.next_prompt : preview(approval.next_prompt)}</pre>
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => void act(api.approveNext)}>
          {busy ? "Working…" : "Approve next"}
        </button>
        <button className="danger" disabled={busy} onClick={() => void act(api.rejectNext)}>
          Reject
        </button>
      </div>
    </div>
  );
}
