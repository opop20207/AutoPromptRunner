import type { ArtifactSummary } from "../types";

// Shows the safety blockers and warnings recorded for a run (from its artifacts).
// Warning contents already describe large-diff and secret-like-file findings.
export function SafetyPanel({ artifacts }: { artifacts: ArtifactSummary[] }) {
  const blockers = artifacts.filter((a) => a.type === "safety_blocker");
  const warnings = artifacts.filter((a) => a.type === "safety_warning");

  if (blockers.length === 0 && warnings.length === 0) {
    return <p className="muted">No safety warnings or blockers.</p>;
  }

  return (
    <div>
      {blockers.length > 0 && (
        <div className="error">
          <strong>Blockers</strong>
          <ul>
            {blockers.map((artifact) => (
              <li key={artifact.id}>{artifact.preview}</li>
            ))}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div style={{ color: "#b8860b" }}>
          <strong>Warnings</strong>
          <ul>
            {warnings.map((artifact) => (
              <li key={artifact.id}>{artifact.preview}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
