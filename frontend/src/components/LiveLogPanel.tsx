import { useEffect, useRef, useState } from "react";

import { api, errorMessage, eventStreamUrl } from "../api/client";
import type { LiveLogMode, RunEvent, RunEventType, RunStatus } from "../types";

const POLL_MS = 2000;

// Event types the SSE stream emits (EventSource dispatches by the `event:` field).
const SSE_TYPES: RunEventType[] = [
  "run_created", "run_queued", "run_started", "step_started", "stdout", "stderr",
  "step_finished", "approval_pending", "run_done", "run_failed", "run_stopped",
  "cancellation_requested", "safety_warning", "lock_acquired", "lock_released", "worker_message",
];

const TERMINAL_TYPES = new Set<string>(["run_done", "run_failed", "run_stopped"]);

function isActive(status: string): boolean {
  return status === "RUNNING" || status === "WAITING_APPROVAL";
}

function statusForEvent(type: string): RunStatus | null {
  switch (type) {
    case "run_done":
      return "DONE";
    case "run_failed":
      return "FAILED";
    case "run_stopped":
      return "STOPPED";
    case "approval_pending":
      return "WAITING_APPROVAL";
    case "run_started":
    case "step_started":
      return "RUNNING";
    default:
      return null;
  }
}

const MODE_LABEL: Record<LiveLogMode, string> = {
  sse: "SSE connected",
  "sse-disconnected": "SSE disconnected",
  polling: "polling fallback",
  paused: "paused",
};

// Live run log: prefers Server-Sent Events (push) and falls back to polling the run logs if
// the SSE connection fails. Deduplicates events by id, preserves manual refresh and
// pause/resume, and never logs the API token (it travels via the SSE URL only).
export function LiveLogPanel({
  runId,
  runStatus,
  onTerminal,
}: {
  runId: number;
  runStatus: RunStatus;
  onTerminal: () => void;
}) {
  const [mode, setMode] = useState<LiveLogMode>("sse");
  const [status, setStatus] = useState<string>(runStatus);
  const [stdout, setStdout] = useState("");
  const [stderr, setStderr] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);

  const onTerminalRef = useRef(onTerminal);
  onTerminalRef.current = onTerminal;
  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<number | null>(null);
  const seenRef = useRef<Set<number>>(new Set());
  const terminalRef = useRef(false);

  function teardown() {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  function ingest(ev: RunEvent) {
    if (seenRef.current.has(ev.id)) return;
    seenRef.current.add(ev.id);
    if (ev.type === "stdout" && ev.message) setStdout((prev) => prev + ev.message + "\n");
    if (ev.type === "stderr" && ev.message) setStderr((prev) => prev + ev.message + "\n");
    const next = statusForEvent(ev.type);
    if (next) setStatus(next);
    setLastUpdated(new Date().toLocaleTimeString());
    if (TERMINAL_TYPES.has(ev.type)) {
      terminalRef.current = true;
      teardown();
      onTerminalRef.current();
    }
  }

  function startPolling(initialMode: LiveLogMode = "polling") {
    setMode(initialMode);
    const poll = async () => {
      try {
        const data = await api.getRunLogs(runId);
        setStdout(data.stdout ?? "");
        setStderr(data.stderr ?? "");
        setStatus(data.status);
        setError(null);
        setLastUpdated(new Date().toLocaleTimeString());
        if (!isActive(data.status)) {
          if (!terminalRef.current) {
            terminalRef.current = true;
            onTerminalRef.current();
          }
          teardown();
        }
      } catch (err) {
        setError(errorMessage(err));
      }
    };
    void poll();
    if (pollRef.current === null) {
      pollRef.current = window.setInterval(() => void poll(), POLL_MS);
    }
  }

  function connect() {
    teardown();
    if (typeof EventSource === "undefined") {
      startPolling();
      return;
    }
    let gotAny = false;
    const es = new EventSource(eventStreamUrl(runId));
    esRef.current = es;
    setMode("sse");
    const handler = (e: MessageEvent) => {
      gotAny = true;
      try {
        ingest(JSON.parse(e.data) as RunEvent);
      } catch {
        // Ignore malformed event data.
      }
    };
    es.onopen = () => setMode("sse");
    for (const type of SSE_TYPES) {
      es.addEventListener(type, handler as EventListener);
    }
    es.onerror = () => {
      // A finished run closes the stream cleanly -> nothing more to do. Otherwise fall back
      // to polling (SSE unavailable/blocked); if we had received events, mark disconnected.
      if (terminalRef.current) {
        teardown();
        return;
      }
      es.close();
      esRef.current = null;
      startPolling(gotAny ? "sse-disconnected" : "polling");
    };
  }

  // (Re)connect when the run changes or pause toggles.
  useEffect(() => {
    seenRef.current = new Set();
    terminalRef.current = false;
    setStdout("");
    setStderr("");
    setError(null);
    setStatus(runStatus);
    if (paused) {
      teardown();
      setMode("paused");
      return;
    }
    connect();
    return teardown;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, paused]);

  async function refresh() {
    // A one-off snapshot from the run logs (works in any mode).
    try {
      const data = await api.getRunLogs(runId);
      setStdout(data.stdout ?? "");
      setStderr(data.stderr ?? "");
      setStatus(data.status);
      setError(null);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="livelog">
      <div className="livelog-head">
        <span>
          status: <strong className="status">{status}</strong>
        </span>
        <span className={"livemode livemode-" + mode}>{MODE_LABEL[mode]}</span>
        <span className="muted">updated {lastUpdated ?? "-"}</span>
        <div className="row-actions">
          <button onClick={() => void refresh()}>Refresh</button>
          <button onClick={() => setPaused((p) => !p)}>{paused ? "Resume" : "Pause"}</button>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="livelog-blocks">
        <div>
          <span className="muted">stdout</span>
          <pre className="block large">{stdout || "(no output yet)"}</pre>
        </div>
        <div>
          <span className="muted">stderr</span>
          <pre className="block large">{stderr || "(no errors)"}</pre>
        </div>
      </div>
    </div>
  );
}
