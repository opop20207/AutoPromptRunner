import { type FormEvent, useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import { PROVIDERS, type ProviderProfile } from "../types";
import { Section } from "./Layout";

// Create or edit a provider profile. Profiles configure how a provider is invoked
// (command, timeout, default args) -- they never store secrets.
export function ProviderForm({
  editing,
  onSaved,
  onCancelEdit,
}: {
  editing: ProviderProfile | null;
  onSaved: () => void;
  onCancelEdit: () => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<string>("mock");
  const [command, setCommand] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("1800");
  const [defaultArgs, setDefaultArgs] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (editing) {
      setName(editing.name);
      setType(editing.type);
      setCommand(editing.command);
      setTimeoutSeconds(String(editing.default_timeout_seconds));
      setDefaultArgs(editing.default_args ?? "");
      setEnabled(editing.enabled);
    }
  }, [editing]);

  function reset() {
    setName("");
    setType("mock");
    setCommand("");
    setTimeoutSeconds("1800");
    setDefaultArgs("");
    setEnabled(true);
    setError(null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const timeout = Number(timeoutSeconds);
    if (!editing && !name.trim()) {
      setError("Name is required.");
      return;
    }
    if (!command.trim()) {
      setError("Command is required.");
      return;
    }
    if (!Number.isInteger(timeout) || timeout < 1) {
      setError("Timeout must be a positive integer.");
      return;
    }
    setBusy(true);
    try {
      if (editing) {
        await api.updateProvider(editing.name, {
          type,
          command: command.trim(),
          default_timeout_seconds: timeout,
          default_args: defaultArgs.trim() || null,
          enabled,
        });
      } else {
        await api.createProvider({
          name: name.trim(),
          type,
          command: command.trim(),
          default_timeout_seconds: timeout,
          default_args: defaultArgs.trim() || null,
          enabled,
        });
        reset();
      }
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title={editing ? `Edit provider "${editing.name}"` : "New provider profile"}>
      <form className="form" onSubmit={submit}>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={!!editing} placeholder="claude-fast" />
        </label>
        <label>
          Type
          <select value={type} onChange={(e) => setType(e.target.value)}>
            {PROVIDERS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label>
          Command (executable; no arguments)
          <input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="claude" />
        </label>
        <label>
          Default timeout (seconds)
          <input
            type="number"
            min={1}
            value={timeoutSeconds}
            onChange={(e) => setTimeoutSeconds(e.target.value)}
          />
        </label>
        <label>
          Default args (space-separated, optional; no secrets)
          <input value={defaultArgs} onChange={(e) => setDefaultArgs(e.target.value)} placeholder="--model sonnet" />
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>

        {error && <p className="error">{error}</p>}
        <div className="row-actions">
          <button type="submit" className="primary" disabled={busy}>
            {busy ? "Saving…" : editing ? "Save changes" : "Create provider"}
          </button>
          {editing && (
            <button type="button" onClick={onCancelEdit} disabled={busy}>
              Cancel edit
            </button>
          )}
        </div>
      </form>
    </Section>
  );
}
