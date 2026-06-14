import { type FormEvent, useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { RunComparisonResponse, RunSummary } from "../types";
import { Section } from "./Layout";
import { StatusBadge } from "./StatusBadge";

// Compare two stored runs side by side (metadata, steps, changed files, diff stats,
// next prompts, artifact counts). Reads only stored DB content via GET /compare/runs.
export function ComparePanel({
  initialA,
  initialB,
  onSelectRun,
}: {
  initialA?: number | null;
  initialB?: number | null;
  onSelectRun: (id: number) => void;
}) {
  const [aId, setAId] = useState(initialA ? String(initialA) : "");
  const [bId, setBId] = useState(initialB ? String(initialB) : "");
  const [showPrompts, setShowPrompts] = useState(false);
  const [recent, setRecent] = useState<RunSummary[]>([]);
  const [result, setResult] = useState<RunComparisonResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listRuns()
      .then(setRecent)
      .catch(() => setRecent([]));
  }, []);

  const compare = useCallback(async (a: string, b: string, prompts: boolean) => {
    const runA = Number(a);
    const runB = Number(b);
    if (!Number.isInteger(runA) || !Number.isInteger(runB) || runA <= 0 || runB <= 0) {
      setError("Enter two valid run ids.");
      return;
    }
    if (runA === runB) {
      setError("Pick two different runs.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setResult(await api.compareRuns({ run_a: runA, run_b: runB, show_prompts: prompts, show_artifacts: true }));
    } catch (err) {
      setError(errorMessage(err));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, []);

  // Auto-compare when opened with both ids preselected (e.g. from the run list / detail).
  useEffect(() => {
    if (initialA && initialB && initialA !== initialB) {
      setAId(String(initialA));
      setBId(String(initialB));
      void compare(String(initialA), String(initialB), false);
    }
  }, [initialA, initialB, compare]);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void compare(aId, bId, showPrompts);
  }

  return (
    <Section title="Compare runs">
      <form className="form" onSubmit={onSubmit}>
        <div className="filters">
          <label>
            Run A id
            <input type="number" min={1} value={aId} onChange={(e) => setAId(e.target.value)} />
          </label>
          <label>
            Recent A
            <select value="" onChange={(e) => e.target.value && setAId(e.target.value)}>
              <option value="">pick…</option>
              {recent.map((r) => (
                <option key={r.id} value={r.id}>
                  #{r.id} {r.status} {r.provider}
                </option>
              ))}
            </select>
          </label>
          <label>
            Run B id
            <input type="number" min={1} value={bId} onChange={(e) => setBId(e.target.value)} />
          </label>
          <label>
            Recent B
            <select value="" onChange={(e) => e.target.value && setBId(e.target.value)}>
              <option value="">pick…</option>
              {recent.map((r) => (
                <option key={r.id} value={r.id}>
                  #{r.id} {r.status} {r.provider}
                </option>
              ))}
            </select>
          </label>
          <label className="checkbox inline">
            <input type="checkbox" checked={showPrompts} onChange={(e) => setShowPrompts(e.target.checked)} />
            Show full prompts
          </label>
        </div>
        <div className="row-actions">
          <button type="submit" className="primary" disabled={loading}>
            {loading ? "Comparing…" : "Compare"}
          </button>
        </div>
      </form>

      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Comparing…</p>}
      {!loading && !error && !result && <p className="muted">Enter two run ids and compare.</p>}

      {result && !loading && <ComparisonView result={result} showPrompts={showPrompts} onSelectRun={onSelectRun} />}
    </Section>
  );
}

function ComparisonView({
  result,
  showPrompts,
  onSelectRun,
}: {
  result: RunComparisonResponse;
  showPrompts: boolean;
  onSelectRun: (id: number) => void;
}) {
  const { run_a: a, run_b: b, steps } = result;
  const flag = (same: boolean) => (same ? "same" : "differ");
  const countTypes = Array.from(
    new Set([
      ...Object.keys(result.artifact_counts_by_type_a.counts),
      ...Object.keys(result.artifact_counts_by_type_b.counts),
    ]),
  ).sort();

  return (
    <div className="detail">
      <p className="muted">{result.summary}</p>

      <table className="table compare-table">
        <thead>
          <tr>
            <th></th>
            <th>
              Run #{a.id} <button className="link-btn" onClick={() => onSelectRun(a.id)}>open</button>
            </th>
            <th>
              Run #{b.id} <button className="link-btn" onClick={() => onSelectRun(b.id)}>open</button>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="muted">Status ({flag(result.same_status)})</td>
            <td><StatusBadge status={a.status} /></td>
            <td><StatusBadge status={b.status} /></td>
          </tr>
          <tr>
            <td className="muted">Provider ({flag(result.same_provider)})</td>
            <td>{a.provider}</td>
            <td>{b.provider}</td>
          </tr>
          <tr>
            <td className="muted">Created</td>
            <td className="mono">{a.created_at}</td>
            <td className="mono">{b.created_at}</td>
          </tr>
          <tr>
            <td className="muted">Steps</td>
            <td>{steps.step_count_a}</td>
            <td>{steps.step_count_b}</td>
          </tr>
          <tr>
            <td className="muted">Failed steps</td>
            <td>{steps.failed_steps_a}</td>
            <td>{steps.failed_steps_b}</td>
          </tr>
          <tr>
            <td className="muted">Exit codes</td>
            <td className="mono">{steps.exit_codes_a.join(", ") || "—"}</td>
            <td className="mono">{steps.exit_codes_b.join(", ") || "—"}</td>
          </tr>
        </tbody>
      </table>

      <div className="subsection">
        <h3>Changed files</h3>
        {result.changed_files.warning && <p className="muted">{result.changed_files.warning}</p>}
        <div className="detail-grid compare-files">
          <FileList title={`Only in #${a.id}`} files={result.changed_files.only_a} />
          <FileList title={`Only in #${b.id}`} files={result.changed_files.only_b} />
        </div>
        <FileList title="Common" files={result.changed_files.common} />
      </div>

      <div className="subsection">
        <h3>Diff stat</h3>
        <div className="detail-grid">
          <pre className="block">{result.diff_stat_a || "(none)"}</pre>
          <pre className="block">{result.diff_stat_b || "(none)"}</pre>
        </div>
      </div>

      <div className="subsection">
        <h3>Latest next prompt</h3>
        <div className="detail-grid">
          <pre className="block">
            {(showPrompts ? result.latest_next_prompt_full_a : result.latest_next_prompt_a) || "(none)"}
          </pre>
          <pre className="block">
            {(showPrompts ? result.latest_next_prompt_full_b : result.latest_next_prompt_b) || "(none)"}
          </pre>
        </div>
      </div>

      <div className="subsection">
        <h3>Artifact counts by type</h3>
        {countTypes.length === 0 ? (
          <p className="muted">No artifacts.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Type</th>
                <th>#{a.id}</th>
                <th>#{b.id}</th>
              </tr>
            </thead>
            <tbody>
              {countTypes.map((type) => (
                <tr key={type}>
                  <td>{type}</td>
                  <td>{result.artifact_counts_by_type_a.counts[type] ?? 0}</td>
                  <td>{result.artifact_counts_by_type_b.counts[type] ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showPrompts && (
        <div className="subsection">
          <h3>Root prompts</h3>
          <div className="detail-grid">
            <pre className="block">{a.root_prompt || "(none)"}</pre>
            <pre className="block">{b.root_prompt || "(none)"}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

function FileList({ title, files }: { title: string; files: string[] }) {
  return (
    <div>
      <p className="muted">
        {title} ({files.length})
      </p>
      {files.length === 0 ? (
        <p className="muted">—</p>
      ) : (
        <ul className="filelist">
          {files.map((f) => (
            <li key={f} className="mono">
              {f}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
