import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { RunDetail as RunDetailData } from "../types";
import { ApprovalPanel } from "./ApprovalPanel";
import { ArtifactList } from "./ArtifactList";
import { ArtifactViewer } from "./ArtifactViewer";
import { CancelPanel } from "./CancelPanel";
import { ChangedFilesPanel } from "./ChangedFilesPanel";
import { DiffStatPanel } from "./DiffStatPanel";
import { Section } from "./Layout";
import { LiveLogPanel } from "./LiveLogPanel";
import { LockPanel } from "./LockPanel";
import { QueuePanel } from "./QueuePanel";
import { SafetyPanel } from "./SafetyPanel";
import { StepList } from "./StepList";

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
  const [artifactRefresh, setArtifactRefresh] = useState(0);
  const [selectedArtifact, setSelectedArtifact] = useState<number | null>(null);

  async function load() {
    if (runId === null) {
      setDetail(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setDetail(await api.getRun(runId));
      setArtifactRefresh((n) => n + 1); // reload the artifact list when the run reloads
    } catch (err) {
      setError(errorMessage(err));
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setSelectedArtifact(null);
    void load();
  }, [runId, refreshKey]);

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
      {runId === null && <p className="muted">Select a run to inspect its steps and artifacts.</p>}
      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error">{error}</p>}
      {detail && !error && (
        <div className="detail">
          <div className="subsection">
            <h3>Summary</h3>
            <dl className="kv">
              <dt>Run</dt>
              <dd>#{detail.id}</dd>
              <dt>Status</dt>
              <dd className="status">{detail.status}</dd>
              <dt>Queue</dt>
              <dd>{detail.queue_status ?? "(not queued)"}</dd>
              <dt>Cancellation</dt>
              <dd>{detail.cancellation_status ?? "(none)"}</dd>
              <dt>Provider</dt>
              <dd>{detail.provider}</dd>
              <dt>Workspace</dt>
              <dd className="mono">{detail.workspace ?? "(none)"}</dd>
              <dt>Root prompt</dt>
              <dd>{detail.prompt}</dd>
              <dt>Max loops</dt>
              <dd>{detail.max_loops}</dd>
              <dt>Created</dt>
              <dd className="mono">{detail.created_at}</dd>
              <dt>Finished</dt>
              <dd className="mono">{detail.finished_at ?? "(none)"}</dd>
            </dl>
          </div>

          <div className="subsection">
            <h3>Live log</h3>
            <LiveLogPanel runId={detail.id} runStatus={detail.status} onTerminal={onChanged} />
          </div>

          <div className="subsection">
            <h3>Safety</h3>
            <SafetyPanel artifacts={detail.artifacts} />
          </div>

          <div className="subsection">
            <h3>Locks</h3>
            <LockPanel runId={detail.id} refreshKey={artifactRefresh} />
          </div>

          <div className="subsection">
            <h3>Queue</h3>
            <QueuePanel runId={detail.id} refreshKey={artifactRefresh} />
          </div>

          <div className="subsection">
            <h3>Cancel</h3>
            <CancelPanel run={detail} onCancelled={onChanged} />
          </div>

          <div className="subsection">
            <h3>Steps ({detail.steps.length})</h3>
            <StepList steps={detail.steps} />
          </div>

          <div className="detail-grid">
            <div className="subsection">
              <h3>Changed files</h3>
              <ChangedFilesPanel artifacts={detail.artifacts} />
            </div>
            <div className="subsection">
              <h3>Diff stat</h3>
              <DiffStatPanel artifacts={detail.artifacts} />
            </div>
          </div>

          <div className="detail-grid">
            <div className="subsection">
              <h3>Artifacts</h3>
              <ArtifactList
                runId={detail.id}
                refreshKey={artifactRefresh}
                selectedId={selectedArtifact}
                onSelect={setSelectedArtifact}
              />
            </div>
            <div className="subsection">
              <h3>Artifact viewer</h3>
              <ArtifactViewer artifactId={selectedArtifact} />
            </div>
          </div>

          {detail.pending_approval && (
            <div className="subsection">
              <h3>Approval</h3>
              <ApprovalPanel
                runId={detail.id}
                approval={detail.pending_approval}
                onResolved={onChanged}
              />
            </div>
          )}
        </div>
      )}
    </Section>
  );
}
