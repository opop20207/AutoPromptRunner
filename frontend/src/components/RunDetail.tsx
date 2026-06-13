import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { RunDetail as RunDetailData } from "../types";
import { ApprovalPanel } from "./ApprovalPanel";
import { Section } from "./Layout";

function latestChangedFiles(detail: RunDetailData): string {
  const items = detail.artifacts.filter((a) => a.type === "changed_files" && a.preview.trim());
  return items.length > 0 ? items[items.length - 1].preview : "";
}

export function RunDetail({
  runId,
  refreshKey,
  onChanged,
}: {
  runId: number | null;
  refreshKey: number;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<RunDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    if (runId === null) {
      setDetail(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setDetail(await api.getRun(runId));
    } catch (err) {
      setError(errorMessage(err));
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [runId, refreshKey]);

  const changed = detail ? latestChangedFiles(detail) : "";

  return (
    <Section
      title="Run Detail"
      actions={
        runId !== null ? (
          <button onClick={() => void load()} disabled={loading}>
            Refresh
          </button>
        ) : undefined
      }
    >
      {runId === null && <p className="muted">Select a run to see its detail.</p>}
      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error">{error}</p>}
      {detail && !error && (
        <>
          <dl className="kv">
            <dt>Run</dt>
            <dd>#{detail.id}</dd>
            <dt>Status</dt>
            <dd className="status">{detail.status}</dd>
            <dt>Provider</dt>
            <dd>{detail.provider}</dd>
            <dt>Workspace</dt>
            <dd className="mono">{detail.workspace ?? "(none)"}</dd>
            <dt>Root prompt</dt>
            <dd>{detail.prompt}</dd>
            <dt>Max loops</dt>
            <dd>{detail.max_loops}</dd>
          </dl>

          {changed && (
            <>
              <p className="muted">Changed files</p>
              <pre className="block">{changed}</pre>
            </>
          )}

          <p className="muted">
            <strong>Steps ({detail.steps.length})</strong>
          </p>
          {detail.steps.length === 0 && <p className="muted">No steps yet.</p>}
          {detail.steps.map((step) => (
            <div className="step" key={step.id}>
              <div className="step-head">
                <span>step #{step.loop_index}</span>
                <span className="status">{step.status}</span>
                <span className="muted">exit {step.exit_code ?? "-"}</span>
              </div>
              {step.stdout && <pre className="block">{step.stdout}</pre>}
              {step.stderr && <pre className="block">{step.stderr}</pre>}
              {step.next_prompt && <p className="muted">next prompt: {step.next_prompt}</p>}
            </div>
          ))}

          {detail.pending_approval && (
            <ApprovalPanel runId={detail.id} approval={detail.pending_approval} onResolved={onChanged} />
          )}
        </>
      )}
    </Section>
  );
}
