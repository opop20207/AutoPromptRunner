import type { ReactNode } from "react";

export function Layout({
  apiBase,
  sidebar,
  children,
}: {
  apiBase: string;
  sidebar?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="app">
      <header className="app-header">
        <h1>AutoPromptRunner</h1>
        <span className="api-base">API: {apiBase}</span>
        <span className="app-tag">local-first · unauthenticated</span>
      </header>
      <div className="app-body">
        {sidebar && <aside className="sidebar">{sidebar}</aside>}
        <main className="app-main">{children}</main>
      </div>
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
