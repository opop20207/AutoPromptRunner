import { useEffect, useRef, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { CommitReview, RunCommit } from "../types";
import { StatusBadge } from "./StatusBadge";

// Local commit workflow for a run's workspace changes. Shows commit readiness, the changed
// files, diff stat, a rule-based (editable) commit message, and any blockers, then lets the
// user propose and -- after checking the confirmation box -- create a LOCAL Git commit. It
// never pushes. Rendered inside RunDetail; reloads run detail after a commit.
export function CommitPanel({
  runId,
  runStatus,
  refreshKey,
  onChanged,
}: {
  runId: number;
  runStatus: string;
  refreshKey: number;
  onChanged: () => void;
}) {
  const [review, setReview] = useState<CommitReview | null>(null);
  const [history, setHistory] = useState<RunCommit[]>([]);
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [allowFailed, setAllowFailed] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const messageTouched = useRef(false);

  async function load(resetMessage = false) {
    setLoading(true);
    setError(null);
    try {
      const [rev, commits] = await Promise.all([
        api.getCommitReview(runId, allowFailed),
        api.listCommits(runId),
      ]);
      setReview(rev);
      setHistory(commits);
      setSelected(new Set(rev.changed_files));
      if (resetMessage || !messageTouched.current) {
        setMessage(rev.proposed_message);
        messageTouched.current = false;
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setNotice(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, refreshKey, allowFailed]);

  function toggleFile(path: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  async function propose() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const record = await api.proposeCommit(runId, allowFailed);
      setNotice(`Proposed commit #${record.id}.`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!confirm) {
      setError("Check the confirmation box before creating a local commit.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const files = review ? review.changed_files.filter((f) => selected.has(f)) : [];
      const result = await api.applyCommit(runId, {
        confirm: true,
        message: message.trim() || null,
        files,
        allow_failed: allowFailed,
      });
      if (result.committed) {
        setNotice(`Created local commit ${(result.commit_hash ?? "").slice(0, 12)} (not pushed).`);
      } else {
        setError(`Commit failed: ${result.error ?? "unknown error"}`);
      }
      setConfirm(false);
      await load();
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const latest = history[0] ?? null;

  return (
    <div>
      <p className="muted">
        Review this run's workspace changes and create a local Git commit.
      </p>
      <p className="warning-text">⚠ This creates a local Git commit only. It does not push.</p>

      {runStatus === "FAILED" && (
        <label className="checkbox-inline">
          <input type="checkbox" checked={allowFailed} onChange={(e) => setAllowFailed(e.target.checked)} />{" "}
          Allow committing this FAILED run's changes
        </label>
      )}

      {error && <p className="error">{error}</p>}
      {notice && <p className="ok">{notice}</p>}
      {loading && !review && <p className="muted">Loading…</p>}

      {review && (
        <div className="commit-card">
          <div className="commit-head">
            <strong>Readiness:</strong>
            <span className={review.ready ? "ok" : "error"}>{review.ready ? "ready" : "not ready"}</span>
            <span className="muted">{review.changed_files.length} changed file(s)</span>
          </div>

          {review.blockers.length > 0 && (
            <div className="warning-box">
              <strong>Blockers</strong>
              <ul>
                {review.blockers.map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
            </div>
          )}

          {review.changed_files.length > 0 && (
            <div className="commit-files">
              <div className="muted">Files to stage (uncheck to exclude):</div>
              {review.changed_files.map((path) => (
                <label key={path} className="checkbox-inline commit-file">
                  <input type="checkbox" checked={selected.has(path)} onChange={() => toggleFile(path)} />{" "}
                  <span className="mono">{path}</span>
                </label>
              ))}
            </div>
          )}

          {review.git_diff_stat.trim() && (
            <pre className="commit-diffstat">{review.git_diff_stat.trim()}</pre>
          )}

          {review.safety_warnings.map((w, i) => (
            <p key={i} className="warning-text">
              ⚠ {w}
            </p>
          ))}

          <label className="commit-message-label">
            Commit message (editable)
            <textarea
              className="commit-message"
              rows={4}
              value={message}
              onChange={(e) => {
                messageTouched.current = true;
                setMessage(e.target.value);
              }}
            />
          </label>

          <div className="row-actions">
            <button onClick={() => void load(true)} disabled={loading || busy}>
              Refresh review
            </button>
            <button onClick={() => void propose()} disabled={busy}>
              Propose commit
            </button>
            <label className="checkbox-inline">
              <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> Confirm
            </label>
            <button
              className="primary"
              onClick={() => void apply()}
              disabled={busy || !review.ready || !confirm || selected.size === 0}
            >
              Apply commit
            </button>
          </div>
        </div>
      )}

      {latest && (
        <div className="subsection">
          <h4>
            Last commit attempt <StatusBadge status={latest.status} />
          </h4>
          <dl className="kv">
            {latest.commit_hash && (
              <>
                <dt>Hash</dt>
                <dd className="mono">{latest.commit_hash}</dd>
              </>
            )}
            <dt>Message</dt>
            <dd>{(latest.commit_message ?? "").split("\n")[0] || "—"}</dd>
            <dt>When</dt>
            <dd className="mono">{latest.committed_at ?? latest.created_at}</dd>
            {latest.error && (
              <>
                <dt>Error</dt>
                <dd className="error">{latest.error}</dd>
              </>
            )}
          </dl>
        </div>
      )}
    </div>
  );
}
