import { type FormEvent, useState } from "react";

import { api, errorMessage } from "../api/client";
import { Section } from "./Layout";

// Splits the comma-separated tags input into a clean list.
function parseTags(raw: string): string[] {
  return raw
    .split(",")
    .map((tag) => tag.trim())
    .filter((tag) => tag.length > 0);
}

export function TemplateForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createTemplate({
        name,
        description,
        tags: parseTags(tags),
        body,
      });
      setName("");
      setDescription("");
      setTags("");
      setBody("");
      onCreated();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="New Template">
      <form className="form" onSubmit={submit}>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label>
          Description
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label>
          Tags (comma-separated)
          <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="tests, fix" />
        </label>
        <label>
          Body
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Fix the failing tests in {{project_name}}. Goal: {{goal}}"
            required
          />
        </label>
        <p className="muted">
          Placeholders: {"{{project_name}}"}, {"{{workspace}}"}, {"{{goal}}"}, {"{{changed_files}}"},{" "}
          {"{{last_error}}"}, {"{{extra_context}}"}. Unknown placeholders are left unchanged.
        </p>
        {error && <p className="error">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Creating…" : "Create template"}
        </button>
      </form>
    </Section>
  );
}
