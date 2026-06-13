import type { ReactNode } from "react";

export function Layout({ apiBase, children }: { apiBase: string; children: ReactNode }) {
  return (
    <div className="app">
      <header className="app-header">
        <h1>AutoPromptRunner</h1>
        <span className="api-base">API: {apiBase}</span>
      </header>
      <main className="app-main">{children}</main>
    </div>
  );
}

export function Section({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="section">
      <div className="section-head">
        <h2>{title}</h2>
        {actions}
      </div>
      <div className="section-body">{children}</div>
    </section>
  );
}
