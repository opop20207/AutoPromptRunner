// A small colored status pill, reusable across runs, queue jobs, approvals, locks,
// cancellations, and worktrees. The colour is derived from the status value via a CSS
// class (`sbadge-<status>`), so no external UI library is needed.
export function StatusBadge({ status }: { status?: string | null }) {
  if (!status) {
    return <span className="sbadge sbadge-none">—</span>;
  }
  const slug = status.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  return <span className={`sbadge sbadge-${slug}`}>{status}</span>;
}
