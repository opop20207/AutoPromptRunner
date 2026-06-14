import { useState } from "react";

import { api } from "./api/client";
import { Dashboard } from "./components/Dashboard";
import { Layout, Section } from "./components/Layout";
import { ProjectForm } from "./components/ProjectForm";
import { ProjectList } from "./components/ProjectList";
import { ProviderForm } from "./components/ProviderForm";
import { ProviderHealthPanel } from "./components/ProviderHealthPanel";
import { ProviderList } from "./components/ProviderList";
import { ComparePanel } from "./components/ComparePanel";
import { QueuePanel } from "./components/QueuePanel";
import { RunDetail } from "./components/RunDetail";
import { RunForm } from "./components/RunForm";
import { RunList } from "./components/RunList";
import { SearchPanel } from "./components/SearchPanel";
import { Sidebar, type SectionKey } from "./components/Sidebar";
import { TemplateForm } from "./components/TemplateForm";
import { TemplateList } from "./components/TemplateList";
import { WorktreeForm } from "./components/WorktreeForm";
import { WorktreeList } from "./components/WorktreeList";
import type { ProviderProfile } from "./types";

export default function App() {
  const [section, setSection] = useState<SectionKey>("overview");
  const [projectRefresh, setProjectRefresh] = useState(0);
  const [templateRefresh, setTemplateRefresh] = useState(0);
  const [worktreeRefresh, setWorktreeRefresh] = useState(0);
  const [runRefresh, setRunRefresh] = useState(0);
  const [detailRefresh, setDetailRefresh] = useState(0);
  const [overviewRefresh, setOverviewRefresh] = useState(0);
  const [selectedRun, setSelectedRun] = useState<number | null>(null);
  const [compareA, setCompareA] = useState<number | null>(null);
  const [compareB, setCompareB] = useState<number | null>(null);
  const [providerRefresh, setProviderRefresh] = useState(0);
  const [editingProvider, setEditingProvider] = useState<ProviderProfile | null>(null);
  const [runProject, setRunProject] = useState("");
  const [runTemplate, setRunTemplate] = useState("");
  const [runWorktree, setRunWorktree] = useState("");

  const refreshProjects = () => setProjectRefresh((n) => n + 1);
  const refreshTemplates = () => setTemplateRefresh((n) => n + 1);
  const refreshWorktrees = () => setWorktreeRefresh((n) => n + 1);
  const refreshProviders = () => setProviderRefresh((n) => n + 1);
  const bumpOverview = () => setOverviewRefresh((n) => n + 1);

  function onRunChanged() {
    setRunRefresh((n) => n + 1);
    setDetailRefresh((n) => n + 1);
    bumpOverview();
  }

  function onRunCreated(runId: number) {
    setSelectedRun(runId);
    setRunRefresh((n) => n + 1);
    setDetailRefresh((n) => n + 1);
    bumpOverview();
    setSection("detail");
  }

  function openRun(runId: number) {
    setSelectedRun(runId);
    setSection("detail");
  }

  function openCompare(a: number, b: number) {
    setCompareA(a);
    setCompareB(b);
    setSection("compare");
  }

  function useAsCompare(slot: "a" | "b", runId: number) {
    if (slot === "a") setCompareA(runId);
    else setCompareB(runId);
  }

  return (
    <Layout
      apiBase={api.base}
      sidebar={<Sidebar active={section} hasDetail={selectedRun !== null} onNavigate={setSection} />}
    >
      {section === "overview" && (
        <Dashboard selectedProject={runProject} refreshKey={overviewRefresh} onNavigate={setSection} />
      )}

      {section === "projects" && (
        <>
          <ProjectForm
            onCreated={() => {
              refreshProjects();
              bumpOverview();
            }}
          />
          <ProjectList refreshKey={projectRefresh} onSelect={setRunProject} />
        </>
      )}

      {section === "templates" && (
        <>
          <TemplateForm onCreated={refreshTemplates} />
          <TemplateList
            refreshKey={templateRefresh}
            selectedName={runTemplate}
            onSelect={setRunTemplate}
            onChanged={refreshTemplates}
          />
        </>
      )}

      {section === "worktrees" && (
        <>
          <WorktreeForm defaultProject={runProject} onCreated={refreshWorktrees} />
          <WorktreeList refreshKey={worktreeRefresh} onChanged={refreshWorktrees} />
        </>
      )}

      {section === "providers" && (
        <>
          <ProviderForm
            editing={editingProvider}
            onSaved={() => {
              setEditingProvider(null);
              refreshProviders();
            }}
            onCancelEdit={() => setEditingProvider(null)}
          />
          <ProviderHealthPanel refreshKey={providerRefresh} />
          <ProviderList
            refreshKey={providerRefresh}
            onChanged={refreshProviders}
            onEdit={(p) => setEditingProvider(p)}
          />
        </>
      )}

      {section === "new-run" && (
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
      )}

      {section === "runs" && (
        <RunList
          refreshKey={runRefresh}
          onSelect={openRun}
          onOpenSearch={() => setSection("search")}
          onOpenCompare={openCompare}
          onViewChain={openRun}
        />
      )}

      {section === "search" && <SearchPanel onSelectRun={openRun} />}

      {section === "compare" && (
        <ComparePanel initialA={compareA} initialB={compareB} onSelectRun={openRun} />
      )}

      {section === "queue" && (
        <Section title="Queue">
          <QueuePanel refreshKey={runRefresh} onChanged={onRunChanged} />
        </Section>
      )}

      {section === "detail" && (
        <RunDetail
          runId={selectedRun}
          refreshKey={detailRefresh}
          onChanged={onRunChanged}
          onUseAsCompare={useAsCompare}
          onOpenRun={openRun}
        />
      )}
    </Layout>
  );
}
