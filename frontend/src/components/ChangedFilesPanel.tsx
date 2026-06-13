import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { ArtifactSummary } from "../types";

function lastOfType(artifacts: ArtifactSummary[], type: string): ArtifactSummary | undefined {
  const matches = artifacts.filter((a) => a.type === type);
  return matches[matches.length - 1];
}

export function ChangedFilesPanel({ artifacts }: { artifacts: ArtifactSummary[] }) {
  const summary = lastOfType(artifacts, "changed_files");
  const [files, setFiles] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!summary) {
      setFiles(null);
      return;
    }
    let cancelled = false;
    setError(null);
    api
      .getArtifact(summary.id)
      .then((artifact) => {
        if (!cancelled) {
          const lines = (artifact.content ?? "").split("\n").map((s) => s.trim()).filter(Boolean);
          setFiles(lines);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [summary?.id]);

  if (!summary) return <p className="muted">No changed files captured.</p>;
  if (error) return <p className="error">{error}</p>;
  if (files === null) return <p className="muted">Loading…</p>;
  if (files.length === 0) return <p className="muted">No changed files.</p>;
  return (
    <ul className="filelist">
      {files.map((file, index) => (
        <li key={index} className="mono">
          {file}
        </li>
      ))}
    </ul>
  );
}
