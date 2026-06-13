import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import { ARTIFACT_TYPES, type ArtifactSummary } from "../types";

export function ArtifactList({
  runId,
  refreshKey,
  selectedId,
  onSelect,
}: {
  runId: number;
  refreshKey: number;
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  const [items, setItems] = useState<ArtifactSummary[]>([]);
  const [filter, setFilter] = useState<string>("all");
  const [contains, setContains] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setItems(await api.getRunArtifacts(runId, filter));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [runId, refreshKey, filter]);

  // Local, client-side narrowing over the already-loaded artifacts (no extra request).
  const needle = contains.trim().toLowerCase();
  const shown = needle
    ? items.filter(
        (a) => a.type.toLowerCase().includes(needle) || a.preview.toLowerCase().includes(needle),
      )
    : items;

  return (
    <div>
      <div className="artifact-filter">
        <label>
          Type
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            {ARTIFACT_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>
        <label>
          Contains
          <input
            value={contains}
            onChange={(e) => setContains(e.target.value)}
            placeholder="filter loaded rows"
          />
        </label>
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && items.length === 0 && <p className="muted">No artifacts.</p>}
      {items.length > 0 && shown.length === 0 && (
        <p className="muted">No artifacts match “{contains}”.</p>
      )}
      {shown.length > 0 && (
        <div className="scroll">
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Type</th>
                <th>Step</th>
                <th>Created</th>
                <th>Preview</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((artifact) => (
                <tr
                  key={artifact.id}
                  className={"clickable" + (artifact.id === selectedId ? " selected" : "")}
                  onClick={() => onSelect(artifact.id)}
                >
                  <td>{artifact.id}</td>
                  <td>{artifact.type}</td>
                  <td>{artifact.step_id ?? "-"}</td>
                  <td className="mono">{artifact.created_at}</td>
                  <td className="mono">{artifact.preview}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
