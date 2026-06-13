import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { Health } from "../types";
import { Section } from "./Layout";

export function HealthPanel() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setHealth(await api.health());
    } catch (err) {
      setHealth(null);
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <Section
      title="Health"
      actions={
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      }
    >
      {loading && <p className="muted">Checking backend…</p>}
      {error && <p className="error">Backend unavailable: {error}</p>}
      {health && !error && (
        <p className="ok">
          status: <strong>{health.status}</strong> — service: <strong>{health.service}</strong>
        </p>
      )}
    </Section>
  );
}
