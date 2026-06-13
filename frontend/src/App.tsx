import { useState } from "react";

import { api } from "./api/client";
import { HealthPanel } from "./components/HealthPanel";
import { Layout } from "./components/Layout";
import { ProjectForm } from "./components/ProjectForm";
import { ProjectList } from "./components/ProjectList";
import { RunDetail } from "./components/RunDetail";
import { RunForm } from "./components/RunForm";
import { RunList } from "./components/RunList";
import { TemplateForm } from "./components/TemplateForm";
import { TemplateList } from "./components/TemplateList";
import { WorktreeForm } from "./components/WorktreeForm";
import { WorktreeList } from "./components/WorktreeList";

export default function App() {
  const [projectRefresh, setProjectRefresh] = useState(0);
  const [templateRefresh, setTemplateRefresh] = useState(0);
  const [worktreeRefresh, setWorktreeRefresh] = useState(0);
  const [runRefresh, setRunRefresh] = useState(0);
  const [detailRefresh, setDetailRefresh] = useState(0);
  const [selectedRun, setSelectedRun] = useState<number | null>(null);
  const [runProject, setRunProject] = useState("");
  const [runTemplate, setRunTemplate] = useState("");
  const [runWorktree, setRunWorktree] = useState("");

  const refreshProjects = () => setProjectRefresh((n) => n + 1);
  const refreshTemplates = () => setTemplateRefresh((n) => n + 1);
  const refreshWorktrees = () => setWorktreeRefresh((n) => n + 1);

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
          <RunForm
            project={runProject}
            template={runTemplate}
            worktree={runWorktree}
            templateRefresh={templateRefresh}
            worktreeRefresh={worktreeRefresh}
            onProjectChange={setRunProject}
            onTemplateChange={setRunTemplate}
            onWorktreeChange={setRunWorktree}
            onCreated={onRunCreated}
          />
          <RunList refreshKey={runRefresh} onSelect={setSelectedRun} />
        </div>
      </div>
      <div className="columns">
        <div className="col">
          <TemplateForm onCreated={refreshTemplates} />
          <WorktreeForm defaultProject={runProject} onCreated={refreshWorktrees} />
        </div>
        <div className="col">
          <TemplateList
            refreshKey={templateRefresh}
            selectedName={runTemplate}
            onSelect={setRunTemplate}
            onChanged={refreshTemplates}
          />
          <WorktreeList refreshKey={worktreeRefresh} onChanged={refreshWorktrees} />
        </div>
      </div>
      <RunDetail runId={selectedRun} refreshKey={detailRefresh} onChanged={onRunChanged} />
    </Layout>
  );
}
