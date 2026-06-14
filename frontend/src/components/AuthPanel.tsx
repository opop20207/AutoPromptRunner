import { useState } from "react";

import { clearToken, hasToken, setToken } from "../api/client";

// Compact API-token control for the header. The token is stored only in this browser's
// localStorage and attached to API requests by the client; it is never displayed after
// saving and never logged.
export function AuthPanel() {
  const [open, setOpen] = useState(false);
  const [stored, setStored] = useState(hasToken());
  const [value, setValue] = useState("");

  function save() {
    if (!value.trim()) return;
    setToken(value);
    setStored(true);
    setValue(""); // never keep the token in component state after saving
    setOpen(false);
  }

  function clear() {
    clearToken();
    setStored(false);
    setValue("");
  }

  return (
    <div className="auth-control">
      <button className="auth-toggle" onClick={() => setOpen((v) => !v)} title="API token">
        {stored ? "🔒 token set" : "🔓 no token"}
      </button>
      {open && (
        <div className="auth-popover">
          <p className="muted">{stored ? "A token is stored in this browser." : "No token stored."}</p>
          <input
            type="password"
            value={value}
            placeholder="Paste API token"
            autoComplete="off"
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
            }}
          />
          <div className="row-actions">
            <button className="primary" onClick={save} disabled={!value.trim()}>
              Save
            </button>
            <button onClick={clear} disabled={!stored}>
              Clear
            </button>
          </div>
          <p className="muted">
            Required only when the backend has auth enabled. Generate one with{" "}
            <code>autoprompt-runner auth token generate</code>.
          </p>
        </div>
      )}
    </div>
  );
}
