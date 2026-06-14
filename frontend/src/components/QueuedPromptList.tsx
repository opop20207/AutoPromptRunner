import { useState } from "react";

import { api, errorMessage } from "../api/client";
import type { QueueSummary } from "../types";
import { StatusBadge } from "./StatusBadge";

// Show a queue's prompts in order. Only PENDING prompts can be reordered. The current
// (WAITING_COMPLETION / next) prompt is highlighted.
export function QueuedPromptList({ summary, onChanged }: { summary: QueueSummary; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const prompts = summary.prompts;

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function preview(text: string): string {
    const norm = text.replace(/\s+/g, " ").trim();
    return norm.length > 80 ? norm.slice(0, 80) + "…" : norm;
  }

  if (prompts.length === 0) {
    return <p className="muted">No prompts in this queue yet. Add one above.</p>;
  }

  return (
    <div>
      {error && <p className="error">{error}</p>}
      <div className="scroll">
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>Status</th>
              <th>Title / preview</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {prompts.map((p) => {
              const isCurrent = summary.current?.id === p.id;
              const canReorder = p.status === "PENDING";
              return (
                <tr key={p.id} className={isCurrent ? "selected" : undefined}>
                  <td>{p.position}</td>
                  <td>
                    <StatusBadge status={p.status} />
                  </td>
                  <td>
                    <strong>{p.title || "(untitled)"}</strong>
                    <div className="muted">{preview(p.prompt)}</div>
                    {p.last_error && <div className="error">⚠ {p.last_error}</div>}
                  </td>
                  <td>
                    <div className="row-actions">
                      <button
                        disabled={busy || !canReorder || p.position <= 1}
                        title={canReorder ? "Move up" : "Only PENDING prompts can be reordered"}
                        onClick={() => void act(() => api.reorderQueuedPrompt(p.id, p.position - 1))}
                      >
                        ↑
                      </button>
                      <button
                        disabled={busy || !canReorder || p.position >= prompts.length}
                        title={canReorder ? "Move down" : "Only PENDING prompts can be reordered"}
                        onClick={() => void act(() => api.reorderQueuedPrompt(p.id, p.position + 1))}
                      >
                        ↓
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
