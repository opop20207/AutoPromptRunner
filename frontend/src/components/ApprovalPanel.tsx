import { useState } from "react";

import { api, errorMessage } from "../api/client";
import type { Approval } from "../types";

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

  async function act(action: (id: number) => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action(runId);
      onResolved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="approval">
      <strong>Pending approval — next prompt</strong>
      <pre className="block">{approval.next_prompt}</pre>
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => void act(api.approveNext)}>
          Approve next
        </button>
        <button className="danger" disabled={busy} onClick={() => void act(api.rejectNext)}>
          Reject
        </button>
      </div>
    </div>
  );
}
