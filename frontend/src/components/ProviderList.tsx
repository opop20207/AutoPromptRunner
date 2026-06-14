import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { ProviderProfile } from "../types";
import { Section } from "./Layout";

// List provider profiles with availability (command discovery only) and per-row actions.
export function ProviderList({
  refreshKey,
  onChanged,
  onEdit,
}: {
  refreshKey: number;
  onChanged: () => void;
  onEdit: (profile: ProviderProfile) => void;
}) {
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setProfiles(await api.listProviders());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [refreshKey]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function seed() {
    await act(() => api.seedProviders());
  }

  async function check(name: string) {
    setError(null);
    try {
      const result = await api.checkProvider(name);
      setProfiles((prev) => prev.map((p) => (p.name === name ? { ...p, available: result.available } : p)));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  function remove(name: string) {
    if (!window.confirm(`Delete provider profile "${name}"? (No external CLI tool is removed.)`)) return;
    void act(() => api.deleteProvider(name));
  }

  return (
    <Section
      title="Provider profiles"
      actions={
        <div className="row-actions">
          <button onClick={() => void seed()} disabled={busy}>
            Seed defaults
          </button>
          <button onClick={() => void load()} disabled={loading}>
            Refresh
          </button>
        </div>
      }
    >
      {error && <p className="error">{error}</p>}
      {loading && profiles.length === 0 && <p className="muted">Loading…</p>}
      {!loading && !error && profiles.length === 0 && (
        <p className="muted">No provider profiles yet. Use “Seed defaults” to create mock / claude-code / codex.</p>
      )}
      {profiles.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Command</th>
              <th>Timeout</th>
              <th>Enabled</th>
              <th>Availability</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {profiles.map((p) => (
              <tr key={p.id}>
                <td>{p.name}</td>
                <td>{p.type}</td>
                <td className="mono">{p.command}</td>
                <td>{p.default_timeout_seconds}s</td>
                <td>
                  <span className={"sbadge " + (p.enabled ? "sbadge-done" : "sbadge-failed")}>
                    {p.enabled ? "enabled" : "disabled"}
                  </span>
                </td>
                <td>
                  <span className={"sbadge " + (p.available ? "sbadge-done" : "sbadge-failed")}>
                    {p.available ? "available" : "unavailable"}
                  </span>
                </td>
                <td>
                  <div className="row-actions">
                    <button onClick={() => onEdit(p)} disabled={busy}>
                      Edit
                    </button>
                    <button onClick={() => void check(p.name)}>Check</button>
                    {p.enabled ? (
                      <button onClick={() => void act(() => api.disableProvider(p.name))} disabled={busy}>
                        Disable
                      </button>
                    ) : (
                      <button onClick={() => void act(() => api.enableProvider(p.name))} disabled={busy}>
                        Enable
                      </button>
                    )}
                    <button className="danger" onClick={() => remove(p.name)} disabled={busy}>
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}
