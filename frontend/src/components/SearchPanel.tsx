import { type FormEvent, useState } from "react";

import { api, errorMessage } from "../api/client";
import {
  PROVIDERS,
  type SearchArtifactResult,
  type SearchRunResult,
  type SearchStepResult,
} from "../types";
import { ArtifactViewer } from "./ArtifactViewer";
import { Section } from "./Layout";
import { StatusBadge } from "./StatusBadge";

const STATUS_OPTIONS = ["", "CREATED", "RUNNING", "WAITING_APPROVAL", "DONE", "FAILED", "STOPPED"];

// SQLite LIKE search over stored runs, steps, and artifacts. Run results open the run
// detail; artifact results load the full content in the inline artifact viewer.
export function SearchPanel({ onSelectRun }: { onSelectRun: (id: number) => void }) {
  const [target, setTarget] = useState<"all" | "runs" | "artifacts">("all");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [provider, setProvider] = useState("");
  const [type, setType] = useState("");
  const [limit, setLimit] = useState(50);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);
  const [runs, setRuns] = useState<SearchRunResult[]>([]);
  const [steps, setSteps] = useState<SearchStepResult[]>([]);
  const [artifacts, setArtifacts] = useState<SearchArtifactResult[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<number | null>(null);

  async function runSearch(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setSelectedArtifact(null);
    try {
      if (target === "runs") {
        setRuns(await api.searchRuns({ q, status, provider, limit }));
        setSteps([]);
        setArtifacts([]);
      } else if (target === "artifacts") {
        setArtifacts(await api.searchArtifacts({ q, type, limit }));
        setRuns([]);
        setSteps([]);
      } else {
        const res = await api.searchAll({ q, limit });
        setRuns(res.runs);
        setSteps(res.steps);
        setArtifacts(res.artifacts);
      }
      setSearched(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  function clear() {
    setQ("");
    setStatus("");
    setProvider("");
    setType("");
    setRuns([]);
    setSteps([]);
    setArtifacts([]);
    setSelectedArtifact(null);
    setSearched(false);
    setError(null);
  }

  const empty = searched && !loading && !error && runs.length === 0 && steps.length === 0 && artifacts.length === 0;

  return (
    <Section title="Search">
      <form className="form" onSubmit={runSearch}>
        <div className="filters">
          <label>
            Target
            <select value={target} onChange={(e) => setTarget(e.target.value as typeof target)}>
              <option value="all">all</option>
              <option value="runs">runs</option>
              <option value="artifacts">artifacts</option>
            </select>
          </label>
          {target !== "artifacts" && (
            <label>
              Status
              <select value={status} onChange={(e) => setStatus(e.target.value)}>
                {STATUS_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s || "(any)"}
                  </option>
                ))}
              </select>
            </label>
          )}
          {target !== "artifacts" && (
            <label>
              Provider
              <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                <option value="">(any)</option>
                {PROVIDERS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
          )}
          {target === "artifacts" && (
            <label>
              Artifact type
              <input value={type} onChange={(e) => setType(e.target.value)} placeholder="e.g. runner_stderr" />
            </label>
          )}
          <label>
            Limit
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {[25, 50, 100].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label>
          Query
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="text to find (case-insensitive)" />
        </label>
        <div className="row-actions">
          <button type="submit" className="primary" disabled={loading}>
            {loading ? "Searching…" : "Search"}
          </button>
          <button type="button" onClick={clear} disabled={loading}>
            Clear
          </button>
        </div>
      </form>

      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Searching…</p>}
      {empty && <p className="muted">No matches.</p>}

      {runs.length > 0 && (
        <div className="subsection">
          <h3>Runs ({runs.length})</h3>
          <table className="table">
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => onSelectRun(r.id)}>
                  <td>#{r.id}</td>
                  <td>
                    <StatusBadge status={r.status} />
                  </td>
                  <td>{r.provider}</td>
                  <td className="preview-cell">{r.prompt_preview}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {steps.length > 0 && (
        <div className="subsection">
          <h3>Steps ({steps.length})</h3>
          <table className="table">
            <tbody>
              {steps.map((s) => (
                <tr key={s.id} className="clickable" onClick={() => onSelectRun(s.run_id)}>
                  <td>run #{s.run_id}</td>
                  <td>step {s.loop_index}</td>
                  <td className="muted">{s.match_field}</td>
                  <td className="preview-cell">{s.match_preview}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {artifacts.length > 0 && (
        <div className="subsection">
          <h3>Artifacts ({artifacts.length})</h3>
          <div className="detail-grid">
            <div className="scroll">
              <table className="table">
                <tbody>
                  {artifacts.map((a) => (
                    <tr
                      key={a.id}
                      className={"clickable" + (a.id === selectedArtifact ? " selected" : "")}
                      onClick={() => setSelectedArtifact(a.id)}
                    >
                      <td>#{a.id}</td>
                      <td>run #{a.run_id}</td>
                      <td>{a.type}</td>
                      <td className="preview-cell">{a.match_preview}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ArtifactViewer artifactId={selectedArtifact} />
          </div>
        </div>
      )}
    </Section>
  );
}
