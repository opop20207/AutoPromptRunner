import { useState } from "react";

import { api } from "./api/client";
import { HealthPanel } from "./components/HealthPanel";
import { Layout } from "./components/Layout";
import { ProjectForm } from "./components/ProjectForm";
import { ProjectList } from "./components/ProjectList";
import { RunDetail } from "./components/RunDetail";
import { RunForm } from "./components/RunForm";
import { RunList } from "./components/RunList";

export default function App() {
  const [projectRefresh, setProjectRefresh] = useState(0);
  const [runRefresh, setRunRefresh] = useState(0);
  const [detailRefresh, setDetailRefresh] = useState(0);
  const [selectedRun, setSelectedRun] = useState<number | null>(null);
  const [runProject, setRunProject] = useState("");

  const refreshProjects = () => setProjectRefresh((n) => n + 1);

  function onRunChanged() {
    setRunRefresh((n) => n + 1);
    setDetailRefresh((n) => n + 1);
  }

  function onRunCreated(runId: number) {
    setSelectedRun(runId);
    setRunRefresh((n) => n + 1);
    setDetailRefresh((n) => n + 1);
  }

  return (
    <Layout apiBase={api.base}>
      <HealthPanel />
      <div className="columns">
        <div className="col">
          <ProjectForm onCreated={refreshProjects} />
          <ProjectList refreshKey={projectRefresh} onSelect={setRunProject} />
        </div>
        <div className="col">
          <RunForm project={runProject} onProjectChange={setRunProject} onCreated={onRunCreated} />
          <RunList refreshKey={runRefresh} onSelect={setSelectedRun} />
        </div>
      </div>
      <RunDetail runId={selectedRun} refreshKey={detailRefresh} onChanged={onRunChanged} />
    </Layout>
  );
}
