import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { Project } from "../types";
import { Section } from "./Layout";

export function ProjectList({
  refreshKey,
  onSelect,
}: {
  refreshKey: number;
  onSelect: (name: string) => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setProjects(await api.listProjects());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [refreshKey]);

  return (
    <Section
      title="Projects"
      actions={
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      }
    >
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && projects.length === 0 && <p className="muted">No projects yet.</p>}
      {projects.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Provider</th>
              <th>Repo path</th>
              <th>Loops</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {projects.map((project) => (
              <tr key={project.id}>
                <td>
                  {project.name}
                  {project.is_default && <span className="badge">default</span>}
                </td>
                <td>{project.default_provider}</td>
                <td className="mono">{project.repo_path}</td>
                <td>{project.default_max_loops}</td>
                <td>
                  <button onClick={() => onSelect(project.name)}>Use</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}
