import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { ArtifactDetail } from "../types";

export function ArtifactViewer({ artifactId }: { artifactId: number | null }) {
  const [artifact, setArtifact] = useState<ArtifactDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (artifactId === null) {
      setArtifact(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setCopied(false);
    api
      .getArtifact(artifactId)
      .then((result) => {
        if (!cancelled) setArtifact(result);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(errorMessage(err));
          setArtifact(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [artifactId]);

  async function copy() {
    if (!artifact?.content) return;
    try {
      await navigator.clipboard.writeText(artifact.content);
      setCopied(true);
    } catch {
      // Clipboard unavailable (e.g. insecure context); ignore silently.
    }
  }

  if (artifactId === null) {
    return <p className="muted">Select an artifact to view its full content.</p>;
  }
  if (loading) return <p className="muted">Loading…</p>;
  if (error) return <p className="error">{error}</p>;
  if (!artifact) return null;

  return (
    <div>
      <div className="artifact-head">
        <span>
          #{artifact.id} — <strong>{artifact.type}</strong>
          {artifact.step_id !== null && <span className="muted"> (step {artifact.step_id})</span>}
        </span>
        <button onClick={() => void copy()} disabled={!artifact.content}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="block large">{artifact.content ?? "(empty)"}</pre>
    </div>
  );
}
