import { useEffect, useMemo, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { RollbackPlan, RunCheckpoint } from "../types";
import { StatusBadge } from "./StatusBadge";

// Run checkpoints + explicit rollback. Checkpoints capture the workspace's Git HEAD before
// each step (read-only). Rollback runs `git reset --hard` and is always explicit: it asks for
// confirmation, requires a separate "force" checkbox to override an unsafe state, and warns
// that it may discard workspace changes. Rendered inside RunDetail; reloads on refreshKey.
export function CheckpointPanel({
  runId,
  runStatus,
  refreshKey,
  onChanged,
}: {
  runId: number;
  runStatus: string;
  refreshKey: number;
  onChanged: () => void;
}) {
  const [checkpoints, setCheckpoints] = useState<RunCheckpoint[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [plan, setPlan] = useState<RollbackPlan | null>(null);
  const [force, setForce] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const items = await api.listCheckpoints(runId);
      setCheckpoints(items);
      setSelectedId((prev) => (prev != null && items.some((c) => c.id === prev) ? prev : items[0]?.id ?? null));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setPlan(null);
    setNotice(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, refreshKey]);

  const selected = useMemo(
    () => checkpoints.find((c) => c.id === selectedId) ?? null,
    [checkpoints, selectedId],
  );
  const dirty = (selected?.git_status_before ?? "").trim().length > 0;

  async function viewPlan(id: number) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setPlan(await api.getRollbackPlan(id));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function rollback(id: number) {
    const ok = window.confirm(
      "Rollback may discard workspace changes.\n\n" +
        `Roll back the workspace to checkpoint #${id} with git reset --hard` +
        (force ? " (FORCE)" : "") +
        "? This cannot be undone.",
    );
    if (!ok) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.rollbackCheckpoint(id, true, force);
      if (result.restored) {
        setNotice(`Rolled back: ${result.message}. HEAD is now ${(result.git_head_after ?? "").slice(0, 12)}.`);
      } else {
        setError(`Rollback failed: ${result.error ?? "unknown error"}`);
      }
      setForce(false);
      await load();
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!loading && checkpoints.length === 0 && !error) {
    return (
      <p className="muted">
        No checkpoints recorded. Git checkpoints are captured automatically before execution when the
        run's workspace is a Git repository.
      </p>
    );
  }

  return (
    <div>
      <p className="muted">
        A checkpoint captures the workspace's Git HEAD before a step. Rollback runs{" "}
        <code>git reset --hard</code> and is never automatic.
      </p>
      {runStatus === "FAILED" && selected && (
        <div className="warning-box">
          This run failed. You can roll the workspace back to a checkpoint below (explicit confirmation
          required).
        </div>
      )}
      {error && <p className="error">{error}</p>}
      {notice && <p className="ok">{notice}</p>}
      {loading && checkpoints.length === 0 && <p className="muted">Loading…</p>}

      {selected && (
        <div className="checkpoint-card">
          <div className="checkpoint-head">
            <strong>Checkpoint #{selected.id}</strong>
            <StatusBadge status={selected.status} />
            <span className="muted">{selected.id === checkpoints[0]?.id ? "latest" : ""}</span>
          </div>
          <dl className="kv">
            <dt>Workspace</dt>
            <dd className="mono">{selected.workspace_path || "—"}</dd>
            <dt>HEAD before</dt>
            <dd className="mono">{selected.git_head_before ?? "—"}</dd>
            <dt>Branch</dt>
            <dd className="mono">{selected.git_branch_before ?? "—"}</dd>
            <dt>Created</dt>
            <dd className="mono">{selected.created_at}</dd>
            {selected.restored_at && (
              <>
                <dt>Restored</dt>
                <dd className="mono">{selected.restored_at}</dd>
              </>
            )}
            {selected.restore_error && (
              <>
                <dt>Note</dt>
                <dd>{selected.restore_error}</dd>
              </>
            )}
          </dl>

          {dirty && (
            <div className="warning-box">
              The workspace had uncommitted changes before the run. Rolling back will discard them too —
              a force rollback is required.
            </div>
          )}

          <p className="warning-text">⚠ Rollback may discard workspace changes.</p>
          <div className="row-actions">
            <button onClick={() => void viewPlan(selected.id)} disabled={busy}>
              View rollback plan
            </button>
            <label className="checkbox-inline">
              <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> Force
              (override unsafe state)
            </label>
            <button className="danger" onClick={() => void rollback(selected.id)} disabled={busy}>
              Roll back
            </button>
          </div>

          {plan && plan.checkpoint_id === selected.id && (
            <div className="rollback-plan">
              <p>{plan.summary}</p>
              <ul className="recon-list">
                <li>
                  target HEAD: <span className="mono">{plan.target_head ?? "—"}</span>
                  {plan.target_branch ? ` (branch ${plan.target_branch})` : ""}
                </li>
                <li>
                  current HEAD: <span className="mono">{plan.current_head ?? "—"}</span>
                </li>
                <li>
                  can rollback: {String(plan.can_rollback)} · safe: {String(plan.safe)} · requires force:{" "}
                  {String(plan.requires_force)}
                </li>
                {plan.warnings.map((w, i) => (
                  <li key={i} className="muted">
                    warning: {w}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {checkpoints.length > 1 && (
        <div className="scroll" style={{ marginTop: 8 }}>
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Step</th>
                <th>Status</th>
                <th>HEAD</th>
                <th>Dirty</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {checkpoints.map((cp) => (
                <tr
                  key={cp.id}
                  className={cp.id === selectedId ? "selected" : undefined}
                  onClick={() => {
                    setSelectedId(cp.id);
                    setPlan(null);
                  }}
                  style={{ cursor: "pointer" }}
                >
                  <td>#{cp.id}</td>
                  <td>{cp.step_id ?? "—"}</td>
                  <td>
                    <StatusBadge status={cp.status} />
                  </td>
                  <td className="mono">{(cp.git_head_before ?? "—").slice(0, 12)}</td>
                  <td>{(cp.git_status_before ?? "").trim() ? "yes" : "no"}</td>
                  <td className="mono">{cp.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
