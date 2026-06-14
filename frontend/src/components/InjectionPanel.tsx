import { useState } from "react";

import { api, errorMessage } from "../api/client";
import type { QueueSummary } from "../types";
import { StatusBadge } from "./StatusBadge";

// Inject the current prompt into the Claude Code app. Safety-first: the user must confirm they
// focused the correct input before the Inject button enables, nothing is injected automatically,
// and after injection the prompt waits until the user marks it complete.
export function InjectionPanel({ summary, onChanged }: { summary: QueueSummary; onChanged: () => void }) {
  const [confirmed, setConfirmed] = useState(false);
  const [restoreClipboard, setRestoreClipboard] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const { queue, target, current, waiting } = summary;
  const paused = queue.status === "PAUSED";
  const terminal = ["DONE", "FAILED", "CANCELLED"].includes(queue.status);
  const targetDisabled = !target || target.status !== "ACTIVE";
  const injectable = current != null && ["PENDING", "READY_TO_INJECT"].includes(current.status);

  async function act(fn: () => Promise<unknown>, okMessage?: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (okMessage) setNotice(okMessage);
      setConfirmed(false);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function preview(text: string): string {
    const norm = text.replace(/\s+/g, " ").trim();
    return norm.length > 240 ? norm.slice(0, 240) + "…" : norm;
  }

  return (
    <div className="injection-panel">
      <div className="commit-head">
        <strong>Selected target:</strong>
        {target ? (
          <>
            <span>{target.name}</span>
            <StatusBadge status={target.status} />
            <span className="muted mono">submit: {target.submit_mode}</span>
          </>
        ) : (
          <span className="error">no app target bound to this queue</span>
        )}
      </div>

      {error && <p className="error">{error}</p>}
      {notice && <p className="ok">{notice}</p>}
      {paused && <div className="warning-box">Queue is paused — resume it to inject.</div>}
      {targetDisabled && target && <div className="warning-box">Target is disabled — enable it to inject.</div>}

      {waiting ? (
        <div className="commit-card">
          <p className="ok">Prompt submitted. Mark complete when Claude Code finishes.</p>
          <p>
            <strong>{waiting.title || "(untitled)"}</strong> <StatusBadge status={waiting.status} />
          </p>
          <div className="row-actions">
            <button
              className="primary"
              disabled={busy}
              onClick={() => void act(() => api.completeCurrentPrompt(queue.id), "Marked complete.")}
            >
              Mark Complete
            </button>
            <button
              className="danger"
              disabled={busy}
              onClick={() => void act(() => api.skipCurrentPrompt(queue.id), "Skipped.")}
            >
              Skip current
            </button>
          </div>
        </div>
      ) : injectable && !terminal ? (
        <div className="commit-card">
          <p>
            <strong>Current prompt:</strong> {current.title || "(untitled)"}{" "}
            <StatusBadge status={current.status} />
          </p>
          <pre className="commit-diffstat">{preview(current.prompt)}</pre>
          <ol className="recon-list">
            <li>Open the Claude Code app.</li>
            <li>Click the correct session/pane input.</li>
            <li>Return here and click Inject.</li>
          </ol>
          <p className="warning-text">⚠ AutoPromptRunner pastes into whatever window is active. Focus the right input first.</p>
          <label className="checkbox-inline">
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I focused
            the correct Claude Code input.
          </label>
          <label className="checkbox-inline">
            <input
              type="checkbox"
              checked={restoreClipboard}
              onChange={(e) => setRestoreClipboard(e.target.checked)}
            />{" "}
            Restore my clipboard afterwards
          </label>
          <div className="row-actions">
            <button
              className="primary"
              disabled={busy || !confirmed || paused || targetDisabled}
              onClick={() =>
                void act(
                  () => api.injectCurrentPrompt(queue.id, restoreClipboard),
                  "Prompt submitted. Mark complete when Claude Code finishes.",
                )
              }
            >
              Inject Current Prompt
            </button>
            <button
              disabled={busy}
              onClick={() => void act(() => api.skipCurrentPrompt(queue.id), "Skipped.")}
            >
              Skip current
            </button>
          </div>
        </div>
      ) : (
        <p className="muted">No prompt is ready to inject.</p>
      )}
    </div>
  );
}
