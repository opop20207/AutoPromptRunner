"""Portable JSON export / import of AutoPromptRunner data.

Exports project profiles, provider profiles, templates, and run history (runs, steps,
approvals, artifacts, recovery attempts) to a single self-describing JSON payload, and
imports such a payload back into the local SQLite database with foreign-key remapping.

It reads only stored database content -- never workspace files, environment variables, or
config files -- and redacts secret-like artifact content by default. Import never deletes
existing data; templates/providers/projects are not overwritten unless the chosen mode
allows it. Standard library only (``json``); no network, no external tools.
"""

from __future__ import annotations

import fnmatch
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import config, storage

FORMAT = "autoprompt-runner-export"
VERSION = 1
SCHEMA_VERSION = 1
REDACTION_PLACEHOLDER = "[REDACTED_BY_AUTOPROMPT_RUNNER_EXPORT]"

# Entity lists in the export payload's ``data`` object, in dependency order.
_ENTITIES = ("projects", "provider_profiles", "templates", "runs", "steps", "approvals", "artifacts", "recovery_attempts")

# Import modes.
IMPORT_MODES = ("merge", "skip_existing", "replace_templates_only")

# Artifact ``type`` tokens that look sensitive (defensive; the system records none of these).
_SENSITIVE_TYPE_TOKENS = ("secret", "credential", "token", "password", "apikey", "api_key", "private_key")


class ExportImportError(Exception):
    """Raised for an invalid payload or bad import mode. ``kind`` is ``"invalid"``."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- redaction ---------------------------------------------------------------


def _looks_sensitive(artifact: dict) -> bool:
    """Whether an artifact's path *name* or type looks secret-like (content is not scanned)."""
    path = (artifact.get("path") or "").strip()
    if path:
        name = os.path.basename(path.rstrip("/\\"))
        if name and any(fnmatch.fnmatch(name, pattern) for pattern in config.SECRET_FILE_PATTERNS):
            return True
    artifact_type = (artifact.get("type") or "").lower()
    return any(token in artifact_type for token in _SENSITIVE_TYPE_TOKENS)


def redact_sensitive_export_data(payload: dict) -> dict:
    """Replace secret-like artifact content with the placeholder and flag that it happened."""
    artifacts = payload.get("data", {}).get("artifacts", [])
    redacted = 0
    for artifact in artifacts:
        if _looks_sensitive(artifact):
            artifact["content"] = REDACTION_PLACEHOLDER
            artifact["redacted"] = True
            redacted += 1
    payload["redacted_artifacts"] = redacted
    return payload


# -- export ------------------------------------------------------------------


def build_export_payload(
    db_path: str,
    *,
    include_projects: bool = True,
    include_providers: bool = True,
    include_templates: bool = True,
    include_runs: bool = True,
    include_artifacts: bool = True,
    include_recoveries: bool = True,
    run_ids: Optional[List[int]] = None,
    project_names: Optional[List[str]] = None,
    artifact_content: bool = True,
    redact_sensitive: bool = True,
    exported_at: Optional[str] = None,
) -> dict:
    """Build a JSON-serializable export payload from stored database content (no disk reads)."""
    data: Dict[str, List[dict]] = {key: [] for key in _ENTITIES}

    all_projects = storage.export_table_rows(db_path, "projects")
    name_filter = set(project_names) if project_names else None
    selected_project_ids = {p["id"] for p in all_projects if name_filter and p["name"] in name_filter}

    if include_projects:
        data["projects"] = [p for p in all_projects if name_filter is None or p["name"] in name_filter]
    if include_providers:
        data["provider_profiles"] = storage.export_table_rows(db_path, "provider_profiles")
    if include_templates:
        data["templates"] = storage.export_table_rows(db_path, "templates")

    included_run_ids: set = set()
    if include_runs:
        runs = storage.export_table_rows(db_path, "runs")
        if run_ids:
            want = set(run_ids)
            runs = [r for r in runs if r["id"] in want]
        if name_filter is not None:
            runs = [r for r in runs if r["project_id"] in selected_project_ids]
        data["runs"] = runs
        included_run_ids = {r["id"] for r in runs}

        if included_run_ids:
            data["steps"] = [s for s in storage.export_table_rows(db_path, "steps") if s["run_id"] in included_run_ids]
            data["approvals"] = [
                a for a in storage.export_table_rows(db_path, "approvals") if a["run_id"] in included_run_ids
            ]
            if include_artifacts:
                artifacts = [
                    a for a in storage.export_table_rows(db_path, "artifacts") if a["run_id"] in included_run_ids
                ]
                if not artifact_content:
                    for artifact in artifacts:
                        artifact["content"] = None
                data["artifacts"] = artifacts
            if include_recoveries:
                data["recovery_attempts"] = [
                    r
                    for r in storage.export_table_rows(db_path, "recovery_attempts")
                    if r["source_run_id"] in included_run_ids or r.get("recovery_run_id") in included_run_ids
                ]

    payload = {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": exported_at or _now_iso(),
        "source": {"app": "AutoPromptRunner", "schema_version": SCHEMA_VERSION},
        "data": data,
    }
    if redact_sensitive:
        payload = redact_sensitive_export_data(payload)
        payload["redacted"] = True
    else:
        payload["redacted"] = False
    return payload


def write_export_file(path: str, payload: dict) -> None:
    """Write an export payload to ``path`` as pretty-printed UTF-8 JSON."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def read_export_file(path: str) -> dict:
    """Read and JSON-parse an export file (raises ExportImportError on malformed JSON)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ExportImportError("invalid", f"file is not valid JSON: {exc}") from exc


# -- validation --------------------------------------------------------------


def validate_export_payload(payload) -> bool:
    """Validate the payload shape and version; raise ExportImportError if it is not importable."""
    if not isinstance(payload, dict):
        raise ExportImportError("invalid", "export payload must be a JSON object")
    if payload.get("format") != FORMAT:
        raise ExportImportError("invalid", f"unrecognized export format (expected '{FORMAT}')")
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ExportImportError("invalid", "export payload is missing an integer 'version'")
    if version < 1:
        raise ExportImportError("invalid", f"invalid export version: {version}")
    if version > VERSION:
        raise ExportImportError(
            "invalid", f"unsupported export version {version} (this build supports up to {VERSION})"
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ExportImportError("invalid", "export payload is missing a 'data' object")
    for key in _ENTITIES:
        if key in data and not isinstance(data[key], list):
            raise ExportImportError("invalid", f"data.{key} must be a list")
    return True


# -- summaries ---------------------------------------------------------------


def summarize_export(payload: dict) -> dict:
    """Return a compact count summary of an export payload (without importing)."""
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    counts = {key: len(data.get(key) or []) for key in _ENTITIES}
    return {
        "format": payload.get("format") if isinstance(payload, dict) else None,
        "version": payload.get("version") if isinstance(payload, dict) else None,
        "exported_at": payload.get("exported_at") if isinstance(payload, dict) else None,
        "redacted": bool(payload.get("redacted")) if isinstance(payload, dict) else False,
        "redacted_artifacts": payload.get("redacted_artifacts", 0) if isinstance(payload, dict) else 0,
        "counts": counts,
    }


def summarize_import_result(counts: Dict[str, Dict[str, int]], mode: str) -> dict:
    """Build a compact import summary from per-entity imported/skipped counts."""
    total_imported = sum(c["imported"] for c in counts.values())
    total_skipped = sum(c["skipped"] for c in counts.values())
    return {"mode": mode, "imported": total_imported, "skipped": total_skipped, "entities": counts}


# -- import ------------------------------------------------------------------


def import_export_payload(db_path: str, payload: dict, mode: str = "merge") -> dict:
    """Validate and import a payload, remapping ids; never deletes existing runs.

    Modes: ``merge`` / ``skip_existing`` (do not overwrite existing config; in
    ``skip_existing`` a run matching an existing run by created_at + root_prompt is skipped)
    and ``replace_templates_only`` (additionally overwrite a template whose name exists).
    """
    validate_export_payload(payload)
    if mode not in IMPORT_MODES:
        raise ExportImportError("invalid", f"unknown import mode '{mode}'. Use one of: {', '.join(IMPORT_MODES)}")

    db_path = storage.init_db(db_path)
    data = payload.get("data", {})

    def rows(key: str) -> List[dict]:
        return data.get(key) or []

    counts = {key: {"imported": 0, "skipped": 0} for key in _ENTITIES}
    project_map: Dict[int, int] = {}
    run_map: Dict[int, int] = {}
    step_map: Dict[int, int] = {}

    # Existing run signatures, for skip_existing run de-duplication.
    existing_run_sigs = {
        (r.get("created_at"), r.get("root_prompt")) for r in storage.export_table_rows(db_path, "runs")
    }

    # Projects (name-unique; never overwritten -- reuse the existing row's id).
    for row in rows("projects"):
        existing = storage.get_project_by_name(db_path, row.get("name"))
        if existing is not None:
            project_map[row["id"]] = existing.id
            counts["projects"]["skipped"] += 1
            continue
        new_id = storage.insert_imported_project(
            db_path, name=row.get("name"), repo_path=row.get("repo_path"),
            default_provider=row.get("default_provider"), default_max_loops=row.get("default_max_loops"),
            require_approval=row.get("require_approval"), timeout_seconds=row.get("timeout_seconds"),
            created_at=row.get("created_at"), updated_at=row.get("updated_at"),
        )
        project_map[row["id"]] = new_id
        counts["projects"]["imported"] += 1

    # Provider profiles (name-unique; never overwritten).
    for row in rows("provider_profiles"):
        if storage.get_provider_profile_by_name(db_path, row.get("name")) is not None:
            counts["provider_profiles"]["skipped"] += 1
            continue
        storage.insert_imported_provider_profile(
            db_path, name=row.get("name"), type=row.get("type"), command=row.get("command"),
            default_timeout_seconds=row.get("default_timeout_seconds"), default_args=row.get("default_args"),
            enabled=row.get("enabled", 1), created_at=row.get("created_at"), updated_at=row.get("updated_at"),
        )
        counts["provider_profiles"]["imported"] += 1

    # Templates (name-unique; overwritten only in replace_templates_only).
    for row in rows("templates"):
        existing = storage.get_template_by_name(db_path, row.get("name"))
        if existing is not None and mode != "replace_templates_only":
            counts["templates"]["skipped"] += 1
            continue
        if existing is not None and mode == "replace_templates_only":
            storage.delete_template(db_path, existing.id)
        storage.insert_imported_template(
            db_path, name=row.get("name"), description=row.get("description"), body=row.get("body"),
            tags=row.get("tags"), created_at=row.get("created_at"), updated_at=row.get("updated_at"),
        )
        counts["templates"]["imported"] += 1

    # Runs (always inserted as new rows; skip_existing de-dups by created_at + root_prompt).
    for row in rows("runs"):
        if mode == "skip_existing" and (row.get("created_at"), row.get("root_prompt")) in existing_run_sigs:
            counts["runs"]["skipped"] += 1
            continue
        project_id = project_map.get(row.get("project_id")) if row.get("project_id") is not None else None
        new_id = storage.insert_imported_run(
            db_path, project_id=project_id, root_prompt=row.get("root_prompt"), provider=row.get("provider"),
            status=row.get("status"), max_loops=row.get("max_loops"), require_approval=row.get("require_approval"),
            created_at=row.get("created_at"), finished_at=row.get("finished_at"),
            workspace=row.get("workspace"), timeout_seconds=row.get("timeout_seconds"),
        )
        run_map[row["id"]] = new_id
        counts["runs"]["imported"] += 1

    # Steps (require an imported parent run).
    for row in rows("steps"):
        new_run_id = run_map.get(row.get("run_id"))
        if new_run_id is None:
            counts["steps"]["skipped"] += 1
            continue
        new_id = storage.insert_imported_step(
            db_path, run_id=new_run_id, loop_index=row.get("loop_index"), prompt=row.get("prompt"),
            stdout=row.get("stdout"), stderr=row.get("stderr"), exit_code=row.get("exit_code"),
            status=row.get("status"), started_at=row.get("started_at"), finished_at=row.get("finished_at"),
            next_prompt=row.get("next_prompt"),
        )
        step_map[row["id"]] = new_id
        counts["steps"]["imported"] += 1

    # Approvals.
    for row in rows("approvals"):
        new_run_id = run_map.get(row.get("run_id"))
        if new_run_id is None:
            counts["approvals"]["skipped"] += 1
            continue
        storage.insert_imported_approval(
            db_path, run_id=new_run_id, step_id=step_map.get(row.get("step_id")),
            next_prompt=row.get("next_prompt"), status=row.get("status"),
            created_at=row.get("created_at"), decided_at=row.get("decided_at"),
        )
        counts["approvals"]["imported"] += 1

    # Artifacts.
    for row in rows("artifacts"):
        new_run_id = run_map.get(row.get("run_id"))
        if new_run_id is None:
            counts["artifacts"]["skipped"] += 1
            continue
        step_id = step_map.get(row.get("step_id")) if row.get("step_id") is not None else None
        storage.insert_imported_artifact(
            db_path, run_id=new_run_id, step_id=step_id, type=row.get("type"),
            content=row.get("content"), path=row.get("path"), created_at=row.get("created_at"),
        )
        counts["artifacts"]["imported"] += 1

    # Recovery attempts.
    for row in rows("recovery_attempts"):
        new_source = run_map.get(row.get("source_run_id"))
        if new_source is None:
            counts["recovery_attempts"]["skipped"] += 1
            continue
        recovery_run_id = run_map.get(row.get("recovery_run_id")) if row.get("recovery_run_id") is not None else None
        failed_step_id = step_map.get(row.get("failed_step_id")) if row.get("failed_step_id") is not None else None
        storage.insert_imported_recovery(
            db_path, source_run_id=new_source, recovery_run_id=recovery_run_id, failed_step_id=failed_step_id,
            status=row.get("status"), recovery_prompt=row.get("recovery_prompt"), reason=row.get("reason"),
            created_at=row.get("created_at"), decided_at=row.get("decided_at"), executed_at=row.get("executed_at"),
        )
        counts["recovery_attempts"]["imported"] += 1

    return summarize_import_result(counts, mode)
