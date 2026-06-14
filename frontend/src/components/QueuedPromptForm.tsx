import { useState } from "react";

import { api, errorMessage } from "../api/client";

// Add a prompt (title + large multiline body) to a queue. Adding never auto-injects.
export function QueuedPromptForm({ queueId, onAdded }: { queueId: number; onAdded: () => void }) {
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt.trim()) {
      setError("Prompt text is required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.addQueuedPrompt(queueId, { title: title.trim() || null, prompt });
      setTitle("");
      setPrompt("");
      onAdded();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="stack">
      <label>
        Prompt title (e.g. Prompt#34)
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Prompt#34" />
      </label>
      <label>
        Prompt
        <textarea
          className="commit-message"
          rows={8}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Paste the full prompt text here…"
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit" className="primary" disabled={busy}>
        Add prompt to queue
      </button>
    </form>
  );
}
