import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { ActiveWindow, AppTarget, VerificationResult } from "../types";
import { StatusBadge } from "./StatusBadge";

// Verify that the active Claude Code window matches a chosen app target before injecting.
// Best-effort: when the active window cannot be read, fall back to manual confirmation.
export function TargetVerificationPanel({ refreshKey }: { refreshKey?: number }) {
  const [targets, setTargets] = useState<AppTarget[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [activeWindow, setActiveWindow] = useState<ActiveWindow | null>(null);
  const [result, setResult] = useState<VerificationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadTargets() {
    setError(null);
    try {
      const items = await api.listAppTargets();
      setTargets(items);
      setSelectedId((prev) => (prev != null && items.some((t) => t.id === prev) ? prev : items[0]?.id ?? null));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  useEffect(() => {
    void loadTargets();
  }, [refreshKey]);

  const selected = targets.find((t) => t.id === selectedId) ?? null;

  async function checkWindow() {
    setBusy(true);
    setError(null);
    try {
      setActiveWindow(await api.getActiveWindow());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    if (selectedId == null) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.verifyAppTarget(selectedId));
      await loadTargets();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function statusKind(status?: string | null): string {
    return status ?? "unknown";
  }

  if (targets.length === 0) {
    return <p className="muted">No app targets to verify yet. Create one above.</p>;
  }

  return (
    <div>
      <p className="muted">
        Reduce the risk of injecting into the wrong session/pane. Verification is best-effort and
        never replaces focusing the correct Claude Code input yourself.
      </p>
      <label className="stack">
        Target
        <select value={selectedId ?? ""} onChange={(e) => setSelectedId(Number(e.target.value))}>
          {targets.map((t) => (
            <option key={t.id} value={t.id}>
              #{t.id} {t.name} [{t.status}]
            </option>
          ))}
        </select>
      </label>

      {error && <p className="error">{error}</p>}

      {selected && (
        <div className="commit-card">
          <div className="commit-head">
            <strong>{selected.name}</strong>
            <StatusBadge status={selected.status} />
            <span className="muted mono">{selected.verification_mode}</span>
          </div>
          <dl className="kv">
            <dt>Expected app</dt>
            <dd>{selected.expected_app_name ?? "—"}</dd>
            <dt>Expected window</dt>
            <dd>{selected.expected_window_title ?? "—"}</dd>
            <dt>Expected session</dt>
            <dd>{selected.expected_session_label ?? "—"}</dd>
            <dt>Expected pane</dt>
            <dd>
              {selected.expected_pane_label ?? "—"}
              {selected.expected_pane_index != null ? ` #${selected.expected_pane_index}` : ""}
            </dd>
            <dt>Last verification</dt>
            <dd>
              {selected.last_verification_status ? (
                <StatusBadge status={selected.last_verification_status} />
              ) : (
                "never"
              )}
            </dd>
          </dl>

          <div className="row-actions">
            <button disabled={busy} onClick={() => void checkWindow()}>
              Check active window
            </button>
            <button className="primary" disabled={busy} onClick={() => void verify()}>
              Verify target
            </button>
          </div>

          {activeWindow && (
            <p className="muted mono">
              Active window:{" "}
              {activeWindow.available
                ? `${activeWindow.title ?? "(no title)"}${activeWindow.app_name ? ` — ${activeWindow.app_name}` : ""}`
                : `unavailable (${activeWindow.reason ?? "unknown"})`}
            </p>
          )}

          {result && (
            <div
              className={
                result.status === "mismatch"
                  ? "warning-box"
                  : result.status === "verified"
                  ? "rollback-plan"
                  : "rollback-plan"
              }
            >
              <p>
                Verification: <StatusBadge status={statusKind(result.status)} /> {result.message}
              </p>
              {result.status === "mismatch" && (
                <p className="warning-text">
                  ⚠ The active window does not match this target. Focus the correct Claude Code
                  session/pane before injecting.
                </p>
              )}
              {result.status === "unavailable" && (
                <p className="muted">Active window could not be read — rely on manual confirmation when injecting.</p>
              )}
              {result.status === "manual_required" && (
                <p className="muted">This target uses manual confirmation — confirm in the Inject panel.</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
