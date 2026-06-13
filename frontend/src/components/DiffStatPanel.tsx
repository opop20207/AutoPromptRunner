import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { ArtifactSummary } from "../types";

function lastOfType(artifacts: ArtifactSummary[], type: string): ArtifactSummary | undefined {
  const matches = artifacts.filter((a) => a.type === type);
  return matches[matches.length - 1];
}

export function DiffStatPanel({ artifacts }: { artifacts: ArtifactSummary[] }) {
  const summary = lastOfType(artifacts, "git_diff_stat");
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!summary) {
      setContent(null);
      return;
    }
    let cancelled = false;
    setError(null);
    api
      .getArtifact(summary.id)
      .then((artifact) => {
        if (!cancelled) setContent(artifact.content ?? "");
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [summary?.id]);

  if (!summary) return <p className="muted">No diff stat captured.</p>;
  if (error) return <p className="error">{error}</p>;
  if (content === null) return <p className="muted">Loading…</p>;
  if (!content.trim()) return <p className="muted">No changes in diff stat.</p>;
  return <pre className="block">{content}</pre>;
}
