import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { AppTarget, PromptQueue, QueueSummary } from "../types";
import { InjectionPanel } from "./InjectionPanel";
import { QueuedPromptForm } from "./QueuedPromptForm";
import { QueuedPromptList } from "./QueuedPromptList";
import { StatusBadge } from "./StatusBadge";

// Claude Code app prompt queue: create queues bound to an app target, add prompts, and inject
// them one at a time. The queue never runs the Claude Code CLI — it injects into the app.
export function PromptQueuePanel({ refreshKey, onChanged }: { refreshKey?: number; onChanged?: () => void }) {
  const [queues, setQueues] = useState<PromptQueue[]>([]);
  const [targets, setTargets] = useState<AppTarget[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [summary, setSummary] = useState<QueueSummary | null>(null);
  const [name, setName] = useState("");
  const [targetId, setTargetId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadLists() {
    setError(null);
    try {
      const [qs, ts] = await Promise.all([api.listPromptQueues(), api.listAppTargets()]);
      setQueues(qs);
      setTargets(ts);
      return qs;
    } catch (err) {
      setError(errorMessage(err));
      return [];
    }
  }

  async function loadSummary(id: number) {
    try {
      setSummary(await api.getPromptQueue(id));
    } catch (err) {
      setError(errorMessage(err));
      setSummary(null);
    }
  }

  async function reload() {
    const qs = await loadLists();
    if (selectedId != null && qs.some((q) => q.id === selectedId)) {
      await loadSummary(selectedId);
    } else {
      setSummary(null);
      setSelectedId(null);
    }
  }

  useEffect(() => {
    void loadLists();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  async function select(id: number) {
    setSelectedId(id);
    await loadSummary(id);
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError("Queue name is required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const queue = await api.createPromptQueue({
        name: name.trim(),
        app_target_id: targetId ? Number(targetId) : null,
      });
      setName("");
      await loadLists();
      await select(queue.id);
      onChanged?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function queueAction(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await reload();
      onChanged?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function onChildChanged() {
    if (selectedId != null) await loadSummary(selectedId);
    await loadLists();
    onChanged?.();
  }

  return (
    <div>
      <form onSubmit={create} className="stack">
        <label>
          Queue name
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="AutoPromptRunner Prompt 34-36" />
        </label>
        <label>
          Bind to app target
          <select value={targetId} onChange={(e) => setTargetId(e.target.value)}>
            <option value="">(none — bind later)</option>
            {targets.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} [{t.status}]
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="primary" disabled={busy}>
          Create queue
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      <div className="subsection">
        <h3>Queues</h3>
        {queues.length === 0 ? (
          <p className="muted">No queues yet.</p>
        ) : (
          <div className="row-actions" style={{ flexWrap: "wrap" }}>
            {queues.map((q) => (
              <button
                key={q.id}
                className={"nav-btn" + (q.id === selectedId ? " active" : "")}
                onClick={() => void select(q.id)}
              >
                #{q.id} {q.name} [{q.status}]
              </button>
            ))}
          </div>
        )}
      </div>

      {summary && (
        <div className="subsection">
          <div className="commit-head">
            <h3 style={{ margin: 0 }}>
              Queue #{summary.queue.id} {summary.queue.name}
            </h3>
            <StatusBadge status={summary.queue.status} />
            <span className="muted">{summary.counts.total ?? 0} prompt(s)</span>
          </div>

          <div className="row-actions" style={{ marginBottom: 8 }}>
            <button disabled={busy} onClick={() => void queueAction(() => api.pausePromptQueue(summary.queue.id))}>
              Pause
            </button>
            <button disabled={busy} onClick={() => void queueAction(() => api.resumePromptQueue(summary.queue.id))}>
              Resume
            </button>
            <button
              className="danger"
              disabled={busy}
              onClick={() => {
                if (window.confirm(`Cancel queue #${summary.queue.id}? Pending prompts become CANCELLED.`)) {
                  void queueAction(() => api.cancelPromptQueue(summary.queue.id));
                }
              }}
            >
              Cancel queue
            </button>
          </div>

          <InjectionPanel summary={summary} onChanged={onChildChanged} />

          <div className="subsection">
            <h3>Prompts</h3>
            <QueuedPromptList summary={summary} onChanged={onChildChanged} />
          </div>

          <div className="subsection">
            <h3>Add a prompt</h3>
            <QueuedPromptForm queueId={summary.queue.id} onAdded={onChildChanged} />
          </div>
        </div>
      )}
    </div>
  );
}
