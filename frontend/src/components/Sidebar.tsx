export type SectionKey =
  | "overview"
  | "projects"
  | "templates"
  | "worktrees"
  | "new-run"
  | "runs"
  | "search"
  | "compare"
  | "queue"
  | "detail";

const SECTIONS: { key: SectionKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "projects", label: "Projects" },
  { key: "templates", label: "Templates" },
  { key: "worktrees", label: "Worktrees" },
  { key: "new-run", label: "New Run" },
  { key: "runs", label: "Runs" },
  { key: "search", label: "Search" },
  { key: "compare", label: "Compare" },
  { key: "queue", label: "Queue" },
];

// Simple local-state navigation -- no routing library. The active section is highlighted;
// "Run Detail" only appears once a run is selected.
export function Sidebar({
  active,
  hasDetail,
  onNavigate,
}: {
  active: SectionKey;
  hasDetail: boolean;
  onNavigate: (section: SectionKey) => void;
}) {
  return (
    <nav className="sidebar-nav">
      {SECTIONS.map((s) => (
        <button
          key={s.key}
          className={"nav-btn" + (active === s.key ? " active" : "")}
          onClick={() => onNavigate(s.key)}
        >
          {s.label}
        </button>
      ))}
      {hasDetail && (
        <button
          className={"nav-btn" + (active === "detail" ? " active" : "")}
          onClick={() => onNavigate("detail")}
        >
          Run Detail
        </button>
      )}
    </nav>
  );
}
