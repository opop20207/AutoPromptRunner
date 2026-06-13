import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { Template } from "../types";
import { Section } from "./Layout";

export function TemplateList({
  refreshKey,
  selectedName,
  onSelect,
  onChanged,
}: {
  refreshKey: number;
  selectedName: string;
  onSelect: (name: string) => void;
  onChanged: () => void;
}) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setTemplates(await api.listTemplates());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [refreshKey]);

  async function seed() {
    setBusy(true);
    setError(null);
    try {
      await api.seedTemplates();
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(name: string) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteTemplate(name);
      if (name === selectedName) onSelect("");
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section
      title="Templates"
      actions={
        <div className="row-actions">
          <button onClick={() => void seed()} disabled={busy}>
            Seed built-ins
          </button>
          <button onClick={() => void load()} disabled={loading}>
            Refresh
          </button>
        </div>
      }
    >
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && templates.length === 0 && (
        <p className="muted">No templates yet. Use “Seed built-ins” to add the defaults.</p>
      )}
      {templates.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Description</th>
              <th>Tags</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {templates.map((template) => (
              <tr key={template.id} className={template.name === selectedName ? "selected" : undefined}>
                <td>{template.name}</td>
                <td>{template.description}</td>
                <td>
                  <span className="tags">
                    {template.tags.map((tag) => (
                      <span key={tag} className="tag">
                        {tag}
                      </span>
                    ))}
                  </span>
                </td>
                <td>
                  <div className="row-actions">
                    <button onClick={() => onSelect(template.name)}>Use</button>
                    <button className="danger" onClick={() => void remove(template.name)} disabled={busy}>
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
