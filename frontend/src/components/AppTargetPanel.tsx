import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import { SUBMIT_MODES, TARGET_MODES, VERIFICATION_MODES, type AppTarget } from "../types";
import { StatusBadge } from "./StatusBadge";

// Manage Claude Code app injection targets. A target names a specific app session/pane (with
// verification expectations) so a prompt is injected where intended -- not just "the app".
export function AppTargetPanel({ refreshKey, onChanged }: { refreshKey?: number; onChanged?: () => void }) {
  const [targets, setTargets] = useState<AppTarget[]>([]);
  const [name, setName] = useState("");
  const [sessionLabel, setSessionLabel] = useState("");
  const [paneLabel, setPaneLabel] = useState("");
  const [paneIndex, setPaneIndex] = useState("");
  const [targetMode, setTargetMode] = useState<string>(TARGET_MODES[0]);
  const [submitMode, setSubmitMode] = useState<string>(SUBMIT_MODES[0]);
  const [verificationMode, setVerificationMode] = useState<string>(VERIFICATION_MODES[0]);
  const [expectedWindowTitle, setExpectedWindowTitle] = useState("");
  const [expectedAppName, setExpectedAppName] = useState("");
  const [expectedProjectPath, setExpectedProjectPath] = useState("");
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
        pane_index: paneIndex.trim() ? Number(paneIndex) : null,
        target_mode: targetMode,
        submit_mode: submitMode,
        verification_mode: verificationMode,
        expected_window_title: expectedWindowTitle.trim() || null,
        expected_app_name: expectedAppName.trim() || null,
        expected_session_label: sessionLabel.trim() || null,
        expected_project_path: expectedProjectPath.trim() || null,
        expected_pane_label: paneLabel.trim() || null,
      });
      setName("");
      setSessionLabel("");
      setPaneLabel("");
      setPaneIndex("");
      setExpectedWindowTitle("");
      setExpectedAppName("");
      setExpectedProjectPath("");
      await load();
      onChanged?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
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
        MVP uses <strong>active window manual injection</strong>. Focus the correct Claude Code input
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
        <div className="row-actions" style={{ gap: 10 }}>
          <label style={{ flex: 1 }}>
            Pane label
            <input value={paneLabel} onChange={(e) => setPaneLabel(e.target.value)} placeholder="left pane" />
          </label>
          <label style={{ width: 110 }}>
            Pane index
            <input value={paneIndex} onChange={(e) => setPaneIndex(e.target.value)} placeholder="0" />
          </label>
        </div>
        <div className="row-actions" style={{ gap: 10 }}>
          <label style={{ flex: 1 }}>
            Target mode
            <select value={targetMode} onChange={(e) => setTargetMode(e.target.value)}>
              {TARGET_MODES.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </label>
          <label style={{ flex: 1 }}>
            Submit mode
            <select value={submitMode} onChange={(e) => setSubmitMode(e.target.value)}>
              {SUBMIT_MODES.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </label>
          <label style={{ flex: 1 }}>
            Verification
            <select value={verificationMode} onChange={(e) => setVerificationMode(e.target.value)}>
              {VERIFICATION_MODES.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </label>
        </div>
        <label>
          Expected window title (verification hint)
          <input value={expectedWindowTitle} onChange={(e) => setExpectedWindowTitle(e.target.value)} placeholder="FactoryColony — Claude" />
        </label>
        <label>
          Expected app name (verification hint)
          <input value={expectedAppName} onChange={(e) => setExpectedAppName(e.target.value)} placeholder="Claude.exe" />
        </label>
        <label>
          Expected project path
          <input value={expectedProjectPath} onChange={(e) => setExpectedProjectPath(e.target.value)} placeholder="C:\\Dev\\FactoryColony" />
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
                <th>Verify mode</th>
                <th>Status</th>
                <th>Last verification</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {targets.map((t) => (
                <tr key={t.id}>
                  <td>#{t.id}</td>
                  <td>{t.name}</td>
                  <td>{t.session_label ?? "—"}</td>
                  <td className="mono">{t.verification_mode}</td>
                  <td>
                    <StatusBadge status={t.status} />
                  </td>
                  <td>{t.last_verification_status ? <StatusBadge status={t.last_verification_status} /> : "—"}</td>
                  <td>
                    <div className="row-actions">
                      <button disabled={busy} onClick={() => void act(() => api.verifyAppTarget(t.id))}>
                        Verify
                      </button>
                      <button disabled={busy} onClick={() => void act(() => (t.status === "ACTIVE" ? api.disableAppTarget(t.id) : api.enableAppTarget(t.id)))}>
                        {t.status === "ACTIVE" ? "Disable" : "Enable"}
                      </button>
                    </div>
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
