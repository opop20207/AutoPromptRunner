import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import { SUBMIT_MODES, TARGET_MODES, type AppTarget } from "../types";
import { StatusBadge } from "./StatusBadge";

// Manage Claude Code app injection targets. A target names a specific app session/pane so a
// prompt is injected into the place the user intends -- not just "the Claude Code app".
export function AppTargetPanel({ refreshKey, onChanged }: { refreshKey?: number; onChanged?: () => void }) {
  const [targets, setTargets] = useState<AppTarget[]>([]);
  const [name, setName] = useState("");
  const [sessionLabel, setSessionLabel] = useState("");
  const [paneLabel, setPaneLabel] = useState("");
  const [targetMode, setTargetMode] = useState<string>(TARGET_MODES[0]);
  const [submitMode, setSubmitMode] = useState<string>(SUBMIT_MODES[0]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setTargets(await api.listAppTargets());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [refreshKey]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError("Target name is required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.createAppTarget({
        name: name.trim(),
        session_label: sessionLabel.trim() || null,
        pane_label: paneLabel.trim() || null,
        target_mode: targetMode,
        submit_mode: submitMode,
      });
      setName("");
      setSessionLabel("");
      setPaneLabel("");
      await load();
      onChanged?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function toggle(target: AppTarget) {
    setBusy(true);
    setError(null);
    try {
      if (target.status === "ACTIVE") await api.disableAppTarget(target.id);
      else await api.enableAppTarget(target.id);
      await load();
      onChanged?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="warning-box">
        MVP uses <strong>active-window manual injection</strong>. Focus the correct Claude Code input
        before injecting — AutoPromptRunner pastes into whatever window is active.
      </div>

      <form onSubmit={create} className="stack">
        <label>
          Target name
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="FactoryColony Claude Session" />
        </label>
        <label>
          Session label
          <input value={sessionLabel} onChange={(e) => setSessionLabel(e.target.value)} placeholder="FactoryColony" />
        </label>
        <label>
          Pane label (optional)
          <input value={paneLabel} onChange={(e) => setPaneLabel(e.target.value)} placeholder="left pane" />
        </label>
        <label>
          Target mode
          <select value={targetMode} onChange={(e) => setTargetMode(e.target.value)}>
            {TARGET_MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label>
          Submit mode
          <select value={submitMode} onChange={(e) => setSubmitMode(e.target.value)}>
            {SUBMIT_MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="primary" disabled={busy}>
          Create app target
        </button>
      </form>

      {error && <p className="error">{error}</p>}
      {loading && targets.length === 0 && <p className="muted">Loading…</p>}
      {!loading && targets.length === 0 && <p className="muted">No app targets yet.</p>}

      {targets.length > 0 && (
        <div className="scroll" style={{ marginTop: 8 }}>
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Session</th>
                <th>Pane</th>
                <th>Mode</th>
                <th>Submit</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {targets.map((t) => (
                <tr key={t.id}>
                  <td>#{t.id}</td>
                  <td>{t.name}</td>
                  <td>{t.session_label ?? "—"}</td>
                  <td>{t.pane_label ?? "—"}</td>
                  <td className="mono">{t.target_mode}</td>
                  <td className="mono">{t.submit_mode}</td>
                  <td>
                    <StatusBadge status={t.status} />
                  </td>
                  <td>
                    <button disabled={busy} onClick={() => void toggle(t)}>
                      {t.status === "ACTIVE" ? "Disable" : "Enable"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
