import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { ProviderProfile } from "../types";
import { Section } from "./Layout";

// Availability summary for each provider profile. Availability is command discovery only
// (the backend uses shutil.which) -- no real Claude Code / Codex prompt is ever executed.
export function ProviderHealthPanel({ refreshKey }: { refreshKey: number }) {
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

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

  return (
    <Section
      title="Provider availability"
      actions={
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      }
    >
      {error && <p className="error">{error}</p>}
      {loading && profiles.length === 0 && <p className="muted">Checking…</p>}
      {!loading && !error && profiles.length === 0 && <p className="muted">No providers configured yet.</p>}
      {profiles.length > 0 && (
        <ul className="health-list">
          {profiles.map((p) => (
            <li key={p.id}>
              <span className={"health-dot " + (p.available ? "ok" : "bad")} aria-hidden="true" />
              <strong>{p.name}</strong> <span className="muted">({p.type})</span>{" "}
              {p.type === "mock"
                ? "always available"
                : p.available
                  ? `command "${p.command}" found`
                  : `command "${p.command}" missing`}
              {!p.enabled && <span className="muted"> · disabled</span>}
            </li>
          ))}
        </ul>
      )}
      <p className="muted">Availability is checked by command discovery only; no agent is executed.</p>
    </Section>
  );
}
