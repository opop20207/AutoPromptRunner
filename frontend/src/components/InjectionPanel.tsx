import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { InjectOutcome, QueueSummary } from "../types";
import { StatusBadge } from "./StatusBadge";

// Inject the current prompt into the Claude Code app. Safety-first: a dry-run builds the target
// safety summary (active window + mismatch) before anything happens; the user must confirm they
// focused the correct input, and a target mismatch requires a second explicit override. Nothing
// is injected automatically; after injection the prompt waits for the user to mark it complete.
export function InjectionPanel({ summary, onChanged }: { summary: QueueSummary; onChanged: () => void }) {
  const [preview, setPreview] = useState<InjectOutcome | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [overrideMismatch, setOverrideMismatch] = useState(false);
  const [restoreClipboard, setRestoreClipboard] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const { queue, target, waiting } = summary;

  async function loadPreview() {
    if (!target) {
      setPreview(null);
      return;
    }
    setError(null);
    try {
      setPreview(await api.injectCurrentPrompt(queue.id, { dry_run: true }));
    } catch (err) {
      setPreview(null);
      setError(errorMessage(err));
    }
  }

  useEffect(() => {
    setConfirmed(false);
    setOverrideMismatch(false);
    setNotice(null);
    void loadPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queue.id, queue.updated_at, target?.id, target?.status, waiting?.id]);

  async function act(fn: () => Promise<unknown>, okMessage?: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (okMessage) setNotice(okMessage);
      setConfirmed(false);
      setOverrideMismatch(false);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function previewText(text: string): string {
    const norm = text.replace(/\s+/g, " ").trim();
    return norm.length > 240 ? norm.slice(0, 240) + "…" : norm;
  }

  if (!target) {
    return <p className="error">This queue has no app target bound. Bind one to inject.</p>;
  }

  const safety = preview?.safety;
  const current = preview?.prompt;
  const paused = queue.status === "PAUSED";
  const terminal = ["DONE", "FAILED", "CANCELLED"].includes(queue.status);
  const mismatch = !!safety?.mismatch;
  const injectReady = confirmed && (!mismatch || overrideMismatch) && !paused && target.status === "ACTIVE";

  return (
    <div className="injection-panel">
      <div className="commit-head">
        <strong>Inject into:</strong>
        <span>{target.name}</span>
        <StatusBadge status={target.status} />
        <span className="muted mono">submit: {target.submit_mode}</span>
      </div>

      {error && <p className="error">{error}</p>}
      {notice && <p className="ok">{notice}</p>}
      {paused && <div className="warning-box">Queue is paused — resume it to inject.</div>}

      {safety && (
        <dl className="kv">
          <dt>Expected session</dt>
          <dd>{safety.expected_session_label ?? "—"}</dd>
          <dt>Expected pane</dt>
          <dd>
            {safety.expected_pane_label ?? "—"}
            {safety.expected_pane_index != null ? ` #${safety.expected_pane_index}` : ""}
          </dd>
          <dt>Active window</dt>
          <dd className="mono">{safety.active_window_summary}</dd>
          <dt>Verification</dt>
          <dd>
            <StatusBadge status={safety.verification_status} /> {safety.verification_message}
          </dd>
        </dl>
      )}

      {safety?.warnings.map((w, i) => (
        <p key={i} className="warning-text">
          ⚠ {w}
        </p>
      ))}

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
      ) : current && !terminal ? (
        <div className="commit-card">
          <p>
            <strong>Current prompt:</strong> {current.title || "(untitled)"}{" "}
            <StatusBadge status={current.status} />
          </p>
          <pre className="commit-diffstat">{previewText(current.prompt)}</pre>
          <ol className="recon-list">
            <li>Open the Claude Code app.</li>
            <li>Click the correct session/pane input.</li>
            <li>Return here and click Inject.</li>
          </ol>
          {mismatch && (
            <div className="warning-box">
              The active window does not match this target. Make sure you focused the right session.
            </div>
          )}
          <label className="checkbox-inline">
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I focused
            the correct Claude Code session/pane input.
          </label>
          {mismatch && (
            <label className="checkbox-inline">
              <input
                type="checkbox"
                checked={overrideMismatch}
                onChange={(e) => setOverrideMismatch(e.target.checked)}
              />{" "}
              Inject anyway despite target mismatch.
            </label>
          )}
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
              disabled={busy || !injectReady}
              onClick={() =>
                void act(
                  () =>
                    api.injectCurrentPrompt(queue.id, {
                      user_confirmed: true,
                      allow_target_mismatch: overrideMismatch,
                      restore_clipboard_after: restoreClipboard,
                    }),
                  "Prompt submitted. Mark complete when Claude Code finishes.",
                )
              }
            >
              Inject Current Prompt
            </button>
            <button disabled={busy} onClick={() => void act(() => api.skipCurrentPrompt(queue.id), "Skipped.")}>
              Skip current
            </button>
            <button disabled={busy} onClick={() => void loadPreview()}>
              Refresh
            </button>
          </div>
        </div>
      ) : (
        <p className="muted">No prompt is ready to inject.</p>
      )}
    </div>
  );
}
