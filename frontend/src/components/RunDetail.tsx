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
import { PromptChainPanel } from "./PromptChainPanel";
import { QueuePanel } from "./QueuePanel";
import { SafetyPanel } from "./SafetyPanel";
import { StatusBadge } from "./StatusBadge";
import { StepList } from "./StepList";

export function RunDetail({
  runId,
  refreshKey,
  onChanged,
  onUseAsCompare,
}: {
  runId: number | null;
  refreshKey: number;
  onChanged: () => void;
  onUseAsCompare?: (slot: "a" | "b", id: number) => void;
}) {
  const [detail, setDetail] = useState<RunDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [artifactRefresh, setArtifactRefresh] = useState(0);
  const [selectedArtifact, setSelectedArtifact] = useState<number | null>(null);
  const [compareNote, setCompareNote] = useState<string | null>(null);

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
    setCompareNote(null);
    void load();
  }, [runId, refreshKey]);

  function pickCompare(slot: "a" | "b") {
    if (runId === null || !onUseAsCompare) return;
    onUseAsCompare(slot, runId);
    setCompareNote(`Saved run #${runId} as compare ${slot.toUpperCase()}.`);
  }

  return (
    <Section
      title="Run Detail"
      actions={
        runId !== null ? (
          <div className="row-actions">
            {onUseAsCompare && (
              <>
                <button onClick={() => pickCompare("a")}>Use as compare A</button>
                <button onClick={() => pickCompare("b")}>Use as compare B</button>
              </>
            )}
            <button onClick={() => void load()} disabled={loading}>
              Refresh
            </button>
          </div>
        ) : undefined
      }
    >
      {runId === null && <p className="muted">Select a run from the Runs list to inspect it.</p>}
      {loading && !detail && <p className="muted">Loading…</p>}
      {error && <p className="error">{error}</p>}
      {compareNote && <p className="ok">{compareNote}</p>}
      {detail && !error && (
        <div className="detail">
          {/* 1. Summary + 2. Current status */}
          <div className="subsection">
            <h3>
              Run #{detail.id} <StatusBadge status={detail.status} />
            </h3>
            <dl className="kv">
              <dt>Provider</dt>
              <dd>{detail.provider}</dd>
              <dt>Queue</dt>
              <dd>{detail.queue_status ? <StatusBadge status={detail.queue_status} /> : "(not queued)"}</dd>
              <dt>Cancellation</dt>
              <dd>{detail.cancellation_status ? <StatusBadge status={detail.cancellation_status} /> : "(none)"}</dd>
              <dt>Workspace</dt>
              <dd className="mono">{detail.workspace ?? "(none)"}</dd>
              <dt>Root prompt</dt>
              <dd>{detail.prompt}</dd>
              <dt>Max loops</dt>
              <dd>{detail.max_loops}</dd>
              <dt>Approval</dt>
              <dd>{detail.require_approval ? "approval gate" : "auto-run"}</dd>
              <dt>Created</dt>
              <dd className="mono">{detail.created_at}</dd>
              <dt>Finished</dt>
              <dd className="mono">{detail.finished_at ?? "(none)"}</dd>
            </dl>
          </div>

          {/* 3. Approval (only when one is pending) */}
          {detail.pending_approval && (
            <div className="subsection">
              <h3>Approval — next prompt</h3>
              <ApprovalPanel runId={detail.id} approval={detail.pending_approval} onResolved={onChanged} />
            </div>
          )}

          {/* 4. Safety */}
          <div className="subsection">
            <h3>Safety</h3>
            <SafetyPanel artifacts={detail.artifacts} />
          </div>

          {/* 5. Steps */}
          <div className="subsection">
            <h3>Steps ({detail.steps.length})</h3>
            <StepList steps={detail.steps} />
          </div>

          {/* 5b. Prompt chain history (root prompt -> step prompts -> next prompts) */}
          <div className="subsection">
            <h3>Prompt chain</h3>
            <PromptChainPanel runId={detail.id} />
          </div>

          {/* 6. Changed files + 7. Diff stat */}
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

          {/* 8. Artifacts (list + full viewer) */}
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

          {/* 9. Logs */}
          <div className="subsection">
            <h3>Logs</h3>
            <LiveLogPanel runId={detail.id} runStatus={detail.status} onTerminal={onChanged} />
          </div>

          {/* Locks + Queue (supporting context) */}
          <div className="detail-grid">
            <div className="subsection">
              <h3>Locks</h3>
              <LockPanel runId={detail.id} refreshKey={artifactRefresh} />
            </div>
            <div className="subsection">
              <h3>Queue</h3>
              <QueuePanel runId={detail.id} refreshKey={artifactRefresh} onChanged={onChanged} />
            </div>
          </div>

          {/* 10. Cancellation */}
          <div className="subsection">
            <h3>Cancellation</h3>
            <CancelPanel run={detail} onCancelled={onChanged} />
          </div>
        </div>
      )}
    </Section>
  );
}
