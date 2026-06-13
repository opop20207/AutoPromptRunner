import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { Health, Project, QueueJob, RunSummary } from "../types";
import { Section } from "./Layout";
import type { SectionKey } from "./Sidebar";

// A compact overview of the backend state: health, recent-run counts, queue state, and
// the default / selected project, plus a reminder to start a worker when jobs are queued.
export function Dashboard({
  selectedProject,
  refreshKey,
  onNavigate,
}: {
  selectedProject: string;
  refreshKey: number;
  onNavigate: (section: SectionKey) => void;
}) {
  const [health, setHealth] = useState<Health | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [queue, setQueue] = useState<QueueJob[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [healthRes, runsRes, queueRes, projectsRes] = await Promise.all([
        api.health().catch(() => null),
        api.listRuns().catch(() => []),
        api.listQueue().catch(() => []),
        api.listProjects().catch(() => []),
      ]);
      setHealth(healthRes);
      setRuns(runsRes);
      setQueue(queueRes);
      setProjects(projectsRes);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [refreshKey]);

  const queuedJobs = queue.filter((j) => j.status === "QUEUED").length;
  const runningJobs = queue.filter((j) => j.status === "RUNNING").length;
  const failedRuns = runs.filter((r) => r.status === "FAILED").length;
  const defaultProject = projects.find((p) => p.is_default)?.name ?? "(none)";

  return (
    <Section
      title="Overview"
      actions={
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      }
    >
      {error && <p className="error">{error}</p>}
      {loading && runs.length === 0 && <p className="muted">Loading…</p>}
      <div className="cards">
        <div className="card">
          <div className="card-label">Backend</div>
          <div className={"card-value small " + (health ? "ok" : "error")}>
            {health ? health.status : "unavailable"}
          </div>
        </div>
        <div className="card">
          <div className="card-label">Recent runs</div>
          <div className="card-value">{runs.length}</div>
        </div>
        <div className="card">
          <div className="card-label">Queued jobs</div>
          <div className="card-value">{queuedJobs}</div>
        </div>
        <div className="card">
          <div className="card-label">Running jobs</div>
          <div className="card-value">{runningJobs}</div>
        </div>
        <div className="card">
          <div className="card-label">Failed runs</div>
          <div className="card-value">{failedRuns}</div>
        </div>
        <div className="card">
          <div className="card-label">Default project</div>
          <div className="card-value small">{defaultProject}</div>
        </div>
        <div className="card">
          <div className="card-label">Selected project</div>
          <div className="card-value small">{selectedProject || "(none)"}</div>
        </div>
      </div>
      {queuedJobs > 0 && (
        <div className="card warn" style={{ marginTop: 12 }}>
          <strong>{queuedJobs} job(s) queued.</strong> Start a worker to execute them
          (<span className="mono">python -m autoprompt_runner.cli worker run</span>), then{" "}
          <button className="link-btn" onClick={() => onNavigate("queue")}>
            view the queue
          </button>
          .
        </div>
      )}
    </Section>
  );
}
