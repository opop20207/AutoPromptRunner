import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { PromptChainNode, PromptChainResponse } from "../types";
import { StatusBadge } from "./StatusBadge";

type ChainFilter = "all" | "failed" | "waiting";

function isFailed(node: PromptChainNode): boolean {
  return node.status.toUpperCase() === "FAILED" || (node.exit_code !== null && node.exit_code !== undefined && node.exit_code !== 0);
}

function nodeArtifactTotal(node: PromptChainNode): number {
  return Object.values(node.artifact_counts_by_type.counts).reduce((sum, n) => sum + n, 0);
}

// Vertical timeline of a run's prompt chain (root prompt -> step prompts -> next prompts,
// with approvals, results, and artifacts). Built from stored data via GET /chains/runs/{id};
// no graph library, plain CSS.
export function PromptChainPanel({ runId }: { runId: number }) {
  const [chain, setChain] = useState<PromptChainResponse | null>(null);
  const [filter, setFilter] = useState<ChainFilter>("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      // Fetch full prompts + artifact counts so expanded nodes have everything locally.
      setChain(await api.getRunChain(runId, { full_prompts: true, include_artifacts: true }));
    } catch (err) {
      setError(errorMessage(err));
      setChain(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [runId]);

  const nodes = chain?.chain_nodes ?? [];
  const shown =
    filter === "failed"
      ? nodes.filter(isFailed)
      : filter === "waiting"
        ? nodes.filter((n) => (n.approval_status ?? "").toUpperCase() === "PENDING")
        : nodes;

  return (
    <div>
      <div className="chain-toolbar">
        <div className="filters">
          <label>
            Filter
            <select value={filter} onChange={(e) => setFilter(e.target.value as ChainFilter)}>
              <option value="all">all</option>
              <option value="failed">failed only</option>
              <option value="waiting">waiting approval only</option>
            </select>
          </label>
          {chain && (
            <span className="muted">
              {chain.step_count} step(s) · {chain.approval_count} approval(s) · {chain.failed_step_count} failed ·{" "}
              {chain.total_artifact_count} artifact(s)
              {chain.pending_approval ? " · pending approval" : ""}
            </span>
          )}
        </div>
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && !chain && <p className="muted">Loading…</p>}
      {chain && !loading && nodes.length === 0 && <p className="muted">No steps yet.</p>}
      {chain && shown.length === 0 && nodes.length > 0 && <p className="muted">No nodes match this filter.</p>}

      {shown.length > 0 && (
        <ol className="chain">
          {shown.map((node) => (
            <ChainNodeItem key={node.node_id} node={node} />
          ))}
        </ol>
      )}
    </div>
  );
}

function ChainNodeItem({ node }: { node: PromptChainNode }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  async function copy(label: string, text: string | null | undefined) {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
    } catch {
      // Clipboard unavailable (insecure context); ignore.
    }
  }

  const counts = Object.entries(node.artifact_counts_by_type.counts);
  const artifactTotal = nodeArtifactTotal(node);

  return (
    <li className={"chain-node" + (isFailed(node) ? " failed" : "")}>
      <div className="chain-node-head">
        <button className="chain-toggle" onClick={() => setOpen((v) => !v)}>
          {open ? "▾" : "▸"}
        </button>
        <span className="chain-loop">loop {node.loop_index}</span>
        <span className="muted">step #{node.step_id}</span>
        <StatusBadge status={node.status} />
        <span className="muted">exit {node.exit_code ?? "—"}</span>
        {node.approval_status && <StatusBadge status={node.approval_status} />}
        <span className="muted">{artifactTotal} artifact(s)</span>
      </div>

      <dl className="chain-kv">
        <dt>Prompt</dt>
        <dd>{node.prompt_preview || "(none)"}</dd>
        <dt>Next prompt</dt>
        <dd>{node.next_prompt_preview || "(none)"}</dd>
        {node.changed_files_preview.length > 0 && (
          <>
            <dt>Changed files</dt>
            <dd className="mono">{node.changed_files_preview.join(", ")}</dd>
          </>
        )}
        {node.stderr_preview && (
          <>
            <dt>stderr</dt>
            <dd className="mono">{node.stderr_preview}</dd>
          </>
        )}
        {node.stdout_preview && (
          <>
            <dt>stdout</dt>
            <dd className="mono">{node.stdout_preview}</dd>
          </>
        )}
      </dl>

      {open && (
        <div className="chain-node-detail">
          <div className="chain-actions">
            <button onClick={() => void copy("prompt", node.prompt ?? node.prompt_preview)}>
              {copied === "prompt" ? "Copied prompt" : "Copy prompt"}
            </button>
            <button onClick={() => void copy("next", node.next_prompt ?? node.next_prompt_preview)}>
              {copied === "next" ? "Copied next prompt" : "Copy next prompt"}
            </button>
          </div>
          <p className="muted">Full prompt</p>
          <pre className="block">{node.prompt ?? node.prompt_preview ?? "(none)"}</pre>
          <p className="muted">Full next prompt</p>
          <pre className="block">{node.next_prompt ?? node.next_prompt_preview ?? "(none)"}</pre>
          {counts.length > 0 && (
            <>
              <p className="muted">Artifact counts</p>
              <ul className="filelist">
                {counts.map(([type, count]) => (
                  <li key={type}>
                    {type}: {count}
                  </li>
                ))}
              </ul>
            </>
          )}
          <dl className="kv compact">
            <dt>Started</dt>
            <dd className="mono">{node.started_at ?? "(none)"}</dd>
            <dt>Finished</dt>
            <dd className="mono">{node.finished_at ?? "(none)"}</dd>
          </dl>
        </div>
      )}
    </li>
  );
}
