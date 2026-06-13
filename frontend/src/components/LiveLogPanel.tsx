import { useEffect, useRef, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { RunLogs, RunStatus } from "../types";

const POLL_MS = 2000;

function isActive(status: string): boolean {
  return status === "RUNNING" || status === "WAITING_APPROVAL";
}

export function LiveLogPanel({
  runId,
  runStatus,
  onTerminal,
}: {
  runId: number;
  runStatus: RunStatus;
  onTerminal: () => void;
}) {
  const [logs, setLogs] = useState<RunLogs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const onTerminalRef = useRef(onTerminal);
  onTerminalRef.current = onTerminal;
  const prevStatusRef = useRef<string | null>(null);

  async function load() {
    try {
      const data = await api.getRunLogs(runId);
      const prev = prevStatusRef.current;
      prevStatusRef.current = data.status;
      setLogs(data);
      setError(null);
      setLastUpdated(new Date().toLocaleTimeString());
      // Reload the full run detail when a run transitions from active to terminal.
      if (prev !== null && isActive(prev) && !isActive(data.status)) {
        onTerminalRef.current();
      }
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  // Reset and load when the selected run changes.
  useEffect(() => {
    prevStatusRef.current = null;
    setLogs(null);
    setError(null);
    void load();
  }, [runId]);

  // Poll every POLL_MS while the run is active and polling is not paused.
  const status: string = logs?.status ?? runStatus;
  useEffect(() => {
    if (paused || !isActive(status)) return;
    const handle = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(handle);
  }, [paused, status, runId]);

  const active = isActive(status);
  const pollState = !active ? "stopped" : paused ? "paused" : "polling…";

  return (
    <div className="livelog">
      <div className="livelog-head">
        <span>
          status: <strong className="status">{status}</strong>
        </span>
        <span className="muted">step #{logs?.latest_step_id ?? "-"}</span>
        <span className="muted">{pollState}</span>
        <span className="muted">updated {lastUpdated ?? "-"}</span>
        <div className="row-actions">
          <button onClick={() => void load()}>Refresh</button>
          <button onClick={() => setPaused((p) => !p)} disabled={!active}>
            {paused ? "Resume" : "Pause"}
          </button>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="livelog-blocks">
        <div>
          <span className="muted">stdout</span>
          <pre className="block large">{logs && logs.stdout ? logs.stdout : "(no output yet)"}</pre>
        </div>
        <div>
          <span className="muted">stderr</span>
          <pre className="block large">{logs && logs.stderr ? logs.stderr : "(no errors)"}</pre>
        </div>
      </div>
    </div>
  );
}
