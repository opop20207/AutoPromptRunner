import { type ChangeEvent, useState } from "react";

import { api, errorMessage } from "../api/client";
import { IMPORT_MODES, type ExportSummary, type ImportSummary } from "../types";
import { Section } from "./Layout";

type IncludeKey =
  | "include_projects"
  | "include_providers"
  | "include_templates"
  | "include_runs"
  | "include_artifacts"
  | "include_recoveries";

const INCLUDE_OPTIONS: { key: IncludeKey; label: string }[] = [
  { key: "include_projects", label: "Projects" },
  { key: "include_providers", label: "Provider profiles" },
  { key: "include_templates", label: "Templates" },
  { key: "include_runs", label: "Runs (+ steps / approvals)" },
  { key: "include_artifacts", label: "Artifacts" },
  { key: "include_recoveries", label: "Recovery attempts" },
];

// Portable JSON export/import of local data. Export downloads a file from the returned
// payload; import reads a local JSON file. Exports may include prompts, stdout, stderr,
// and artifact content -- redaction of secret-like artifacts is best-effort.
export function ExportImportPanel() {
  const [include, setInclude] = useState<Record<IncludeKey, boolean>>({
    include_projects: true,
    include_providers: true,
    include_templates: true,
    include_runs: true,
    include_artifacts: true,
    include_recoveries: true,
  });
  const [artifactContent, setArtifactContent] = useState(true);
  const [redact, setRedact] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportNote, setExportNote] = useState<string | null>(null);

  const [fileText, setFileText] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string>("");
  const [mode, setMode] = useState<string>("merge");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [summary, setSummary] = useState<ExportSummary | null>(null);
  const [result, setResult] = useState<ImportSummary | null>(null);

  async function runExport() {
    setExporting(true);
    setExportError(null);
    setExportNote(null);
    try {
      const payload = await api.exportData({
        ...include,
        artifact_content: artifactContent,
        redact_sensitive: redact,
      });
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "autoprompt-export.json";
      anchor.click();
      URL.revokeObjectURL(url);
      const counts = payload.data ? Object.entries(payload.data).map(([k, v]) => `${k}=${(v as unknown[]).length}`) : [];
      setExportNote(`Downloaded autoprompt-export.json (${counts.join(", ")}).`);
    } catch (err) {
      setExportError(errorMessage(err));
    } finally {
      setExporting(false);
    }
  }

  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    setSummary(null);
    setResult(null);
    setImportError(null);
    if (!file) {
      setFileText(null);
      setFileName("");
      return;
    }
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = () => setFileText(typeof reader.result === "string" ? reader.result : null);
    reader.onerror = () => setImportError("Could not read the selected file.");
    reader.readAsText(file);
  }

  function parsePayload(): unknown {
    if (!fileText) throw new Error("Select a JSON export file first.");
    try {
      return JSON.parse(fileText);
    } catch {
      throw new Error("Selected file is not valid JSON.");
    }
  }

  async function previewSummary() {
    setImportError(null);
    setResult(null);
    try {
      setSummary(await api.summarizeExport(parsePayload()));
    } catch (err) {
      setImportError(errorMessage(err));
    }
  }

  async function runImport() {
    setImporting(true);
    setImportError(null);
    setResult(null);
    try {
      setResult(await api.importData(parsePayload(), mode));
    } catch (err) {
      setImportError(errorMessage(err));
    } finally {
      setImporting(false);
    }
  }

  return (
    <>
      <Section title="Export data">
        <p className="warn-note">
          ⚠ Exports may include prompts, stdout, stderr, and artifact content. Secret-like artifacts are redacted
          on a best-effort basis only — review before sharing.
        </p>
        <div className="filters">
          {INCLUDE_OPTIONS.map((opt) => (
            <label key={opt.key} className="checkbox inline">
              <input
                type="checkbox"
                checked={include[opt.key]}
                onChange={(e) => setInclude({ ...include, [opt.key]: e.target.checked })}
              />
              {opt.label}
            </label>
          ))}
        </div>
        <div className="filters">
          <label className="checkbox inline">
            <input type="checkbox" checked={artifactContent} onChange={(e) => setArtifactContent(e.target.checked)} />
            Include artifact content
          </label>
          <label className="checkbox inline">
            <input type="checkbox" checked={redact} onChange={(e) => setRedact(e.target.checked)} />
            Redact secret-like content
          </label>
        </div>
        {exportError && <p className="error">{exportError}</p>}
        {exportNote && <p className="ok">{exportNote}</p>}
        <div className="row-actions">
          <button className="primary" onClick={() => void runExport()} disabled={exporting}>
            {exporting ? "Exporting…" : "Export & download JSON"}
          </button>
        </div>
      </Section>

      <Section title="Import data">
        <p className="warn-note">
          ⚠ Do not import untrusted files without review. Import never deletes existing runs; existing templates /
          providers / projects are not overwritten (except templates in “replace templates only”).
        </p>
        <div className="filters">
          <label>
            File
            <input type="file" accept="application/json,.json" onChange={onFile} />
          </label>
          <label>
            Mode
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              {IMPORT_MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        </div>
        {fileName && <p className="muted">Selected: {fileName}</p>}
        {importError && <p className="error">{importError}</p>}
        <div className="row-actions">
          <button onClick={() => void previewSummary()} disabled={!fileText}>
            Preview summary
          </button>
          <button className="primary" onClick={() => void runImport()} disabled={!fileText || importing}>
            {importing ? "Importing…" : "Import"}
          </button>
        </div>

        {summary && (
          <div className="subsection">
            <h3>Summary</h3>
            <p className="muted">
              format {summary.format ?? "?"} v{summary.version ?? "?"} · redacted={String(summary.redacted)} (
              {summary.redacted_artifacts} artifact(s))
            </p>
            <p className="mono">
              {Object.entries(summary.counts)
                .map(([k, v]) => `${k}=${v}`)
                .join(", ")}
            </p>
          </div>
        )}

        {result && (
          <div className="subsection">
            <h3>Import result ({result.mode})</h3>
            <p>
              {result.imported} imported, {result.skipped} skipped.
            </p>
            <ul className="filelist">
              {Object.entries(result.entities)
                .filter(([, v]) => v.imported || v.skipped)
                .map(([name, v]) => (
                  <li key={name}>
                    {name}: {v.imported} imported, {v.skipped} skipped
                  </li>
                ))}
            </ul>
          </div>
        )}
      </Section>
    </>
  );
}
