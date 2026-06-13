import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { RunLock } from "../types";
import { StatusBadge } from "./StatusBadge";

// Shows workspace execution locks. When `runId` is given, the current run's lock row is
// highlighted. ACTIVE locks can be released manually (with a confirmation) as an escape
// hatch for stale locks.
export function LockPanel({ runId, refreshKey }: { runId?: number; refreshKey?: number }) {
  const [locks, setLocks] = useState<RunLock[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setLocks(await api.listLocks());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [refreshKey]);

  async function release(rid: number) {
    if (!window.confirm(`Release the workspace lock for run ${rid}?`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.releaseLock(rid);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && locks.length === 0 && <p className="muted">No locks.</p>}
      {locks.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Expires</th>
              <th>Workspace</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {locks.map((lock) => (
              <tr key={lock.id} className={runId && lock.run_id === runId ? "selected" : undefined}>
                <td>#{lock.run_id}</td>
                <td>
                  <StatusBadge status={lock.status} />
                </td>
                <td className="mono">{lock.expires_at ?? "—"}</td>
                <td className="mono">{lock.workspace_path}</td>
                <td>
                  {lock.status === "ACTIVE" && (
                    <button className="danger" disabled={busy} onClick={() => void release(lock.run_id)}>
                      Release
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
