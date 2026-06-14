"""SQLite persistence for AutoPromptRunner.

A thin data-access layer over the Python standard-library ``sqlite3`` module. It
stores project profiles, runs, steps, approvals, artifacts, and a small settings table
(for the default project) so state survives across CLI invocations. All state stays on
the local machine (see AGENTS.md, "Logging Rules"). No third-party packages are used.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import settings as _settings
from .approvals import ApprovalStatus
from .models import (
    Approval,
    Artifact,
    Project,
    ProviderProfile,
    QueueJob,
    RecoveryAttempt,
    RunCancellation,
    RunLock,
    StoredRun,
    StoredStep,
    Template,
    Worktree,
    WorkerHeartbeat,
)
from .state import RunStatus, TERMINAL_STATUSES, validate_status_transition

# Built-in default database location, relative to the current working directory. The
# effective default comes from settings (config file + AUTOPROMPT_DB_PATH); this constant
# is the fallback used when settings cannot be loaded.
DEFAULT_DB_PATH = os.path.join(".autoprompt", "autoprompt.db")

# Settings key that stores the default project's id.
DEFAULT_PROJECT_KEY = "default_project_id"

# Run-lock statuses (mirrored as constants in autoprompt_runner.locks).
LOCK_ACTIVE = "ACTIVE"
LOCK_RELEASED = "RELEASED"
LOCK_EXPIRED = "EXPIRED"

# Run-queue job statuses (mirrored as constants in autoprompt_runner.queue).
QUEUE_QUEUED = "QUEUED"
QUEUE_RUNNING = "RUNNING"
QUEUE_DONE = "DONE"
QUEUE_FAILED = "FAILED"
QUEUE_CANCELLED = "CANCELLED"
# Statuses for which a run may not be enqueued again (it already has an active job).
_QUEUE_ACTIVE_STATUSES = (QUEUE_QUEUED, QUEUE_RUNNING)

# Run-cancellation statuses (mirrored as constants in autoprompt_runner.cancel).
CANCELLATION_REQUESTED = "REQUESTED"
CANCELLATION_COMPLETED = "COMPLETED"
CANCELLATION_FAILED = "FAILED"

# Worker heartbeat statuses (see autoprompt_runner.reconcile / worker).
WORKER_ACTIVE = "ACTIVE"
WORKER_STOPPED = "STOPPED"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    repo_path TEXT,
    default_provider TEXT,
    default_max_loops INTEGER,
    require_approval INTEGER,
    timeout_seconds INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_name ON projects (name);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER,
    root_prompt TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    max_loops INTEGER NOT NULL,
    require_approval INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    workspace TEXT,
    timeout_seconds INTEGER,
    FOREIGN KEY (project_id) REFERENCES projects (id)
);

CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    loop_index INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    stdout TEXT,
    stderr TEXT,
    exit_code INTEGER,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    next_prompt TEXT,
    FOREIGN KEY (run_id) REFERENCES runs (id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    step_id INTEGER NOT NULL,
    next_prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs (id),
    FOREIGN KEY (step_id) REFERENCES steps (id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    step_id INTEGER,
    type TEXT NOT NULL,
    content TEXT,
    path TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (id),
    FOREIGN KEY (step_id) REFERENCES steps (id)
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    body TEXT NOT NULL,
    tags TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_templates_name ON templates (name);

CREATE TABLE IF NOT EXISTS worktrees (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    branch TEXT NOT NULL,
    path TEXT NOT NULL,
    base_branch TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_worktrees_name ON worktrees (name);

CREATE TABLE IF NOT EXISTS run_locks (
    id INTEGER PRIMARY KEY,
    workspace_path TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    owner TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_locks_workspace ON run_locks (workspace_path, status);

CREATE TABLE IF NOT EXISTS run_queue (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    last_error TEXT,
    FOREIGN KEY (run_id) REFERENCES runs (id)
);

CREATE INDEX IF NOT EXISTS idx_run_queue_status ON run_queue (status, priority, created_at);

CREATE TABLE IF NOT EXISTS run_cancellations (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    requested_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    FOREIGN KEY (run_id) REFERENCES runs (id)
);

CREATE INDEX IF NOT EXISTS idx_run_cancellations_run ON run_cancellations (run_id);

CREATE TABLE IF NOT EXISTS provider_profiles (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    command TEXT NOT NULL,
    default_timeout_seconds INTEGER NOT NULL,
    default_args TEXT,
    enabled INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_profiles_name ON provider_profiles (name);

CREATE TABLE IF NOT EXISTS recovery_attempts (
    id INTEGER PRIMARY KEY,
    source_run_id INTEGER NOT NULL,
    recovery_run_id INTEGER,
    failed_step_id INTEGER,
    status TEXT NOT NULL,
    recovery_prompt TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    executed_at TEXT,
    FOREIGN KEY (source_run_id) REFERENCES runs (id),
    FOREIGN KEY (recovery_run_id) REFERENCES runs (id),
    FOREIGN KEY (failed_step_id) REFERENCES steps (id)
);

CREATE INDEX IF NOT EXISTS idx_recovery_attempts_source ON recovery_attempts (source_run_id);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    step_id INTEGER,
    type TEXT NOT NULL,
    message TEXT,
    payload TEXT,
    created_at TEXT NOT NULL
);

-- No foreign key on run_events: it is an append-only telemetry log, so emitting an event
-- must never fail (best-effort), and events may outlive a deleted run.
CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events (run_id, id);

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    id INTEGER PRIMARY KEY,
    worker_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stopped_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_status ON worker_heartbeats (status, updated_at);
"""

# Columns added after the initial schema. Applied idempotently for backward
# compatibility with databases created before these columns existed.
_RUNS_MIGRATIONS = (
    ("workspace", "ALTER TABLE runs ADD COLUMN workspace TEXT"),
    ("timeout_seconds", "ALTER TABLE runs ADD COLUMN timeout_seconds INTEGER"),
)
_PROJECTS_MIGRATIONS = (
    ("default_provider", "ALTER TABLE projects ADD COLUMN default_provider TEXT"),
    ("default_max_loops", "ALTER TABLE projects ADD COLUMN default_max_loops INTEGER"),
    ("require_approval", "ALTER TABLE projects ADD COLUMN require_approval INTEGER"),
    ("timeout_seconds", "ALTER TABLE projects ADD COLUMN timeout_seconds INTEGER"),
    ("updated_at", "ALTER TABLE projects ADD COLUMN updated_at TEXT"),
)


def _utcnow_iso() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp, returning ``None`` on absence or bad input."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _default_db_path() -> str:
    """The configured default db path (settings), falling back to ``DEFAULT_DB_PATH``."""
    try:
        return _settings.load_settings().storage.db_path or DEFAULT_DB_PATH
    except Exception:
        return DEFAULT_DB_PATH


def _resolve_db_path(db_path: Optional[str]) -> str:
    return db_path or _default_db_path()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_table(conn: sqlite3.Connection, table: str, migrations) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for column, statement in migrations:
        if column not in existing:
            conn.execute(statement)


def _opt(row: sqlite3.Row, column: str):
    """Return ``row[column]`` if the column is present, else ``None`` (older rows)."""
    return row[column] if column in row.keys() else None


def init_db(db_path: Optional[str] = None) -> str:
    """Create the database file and tables if absent. Return the resolved path.

    The parent directory is created when it does not yet exist, and any
    backward-compatible column migrations are applied.
    """
    path = _resolve_db_path(db_path)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    conn = _connect(path)
    try:
        conn.executescript(SCHEMA)
        _migrate_table(conn, "runs", _RUNS_MIGRATIONS)
        _migrate_table(conn, "projects", _PROJECTS_MIGRATIONS)
        conn.commit()
    finally:
        conn.close()
    return path


# -- project profiles --------------------------------------------------------


def create_project(
    db_path: str,
    name: str,
    repo_path: str,
    default_provider: str,
    default_max_loops: int,
    require_approval: bool,
    timeout_seconds: int,
) -> int:
    """Insert a project profile and return its id. Raises on a duplicate name."""
    path = _resolve_db_path(db_path)
    now = _utcnow_iso()
    conn = _connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO projects "
            "(name, repo_path, default_provider, default_max_loops, require_approval, "
            "timeout_seconds, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                repo_path,
                default_provider,
                int(default_max_loops),
                1 if require_approval else 0,
                int(timeout_seconds),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_projects(db_path: str) -> List[Project]:
    """Return all project profiles ordered by name."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        rows = conn.execute("SELECT * FROM projects ORDER BY name ASC").fetchall()
        return [_row_to_project(row) for row in rows]
    finally:
        conn.close()


def get_project_by_id(db_path: str, project_id: int) -> Optional[Project]:
    """Return the project with ``project_id`` or ``None``."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _row_to_project(row) if row is not None else None
    finally:
        conn.close()


def get_project_by_name(db_path: str, name: str) -> Optional[Project]:
    """Return the project named ``name`` or ``None``."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        row = conn.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
        return _row_to_project(row) if row is not None else None
    finally:
        conn.close()


def update_project(
    db_path: str,
    project_id: int,
    repo_path: Optional[str] = None,
    default_provider: Optional[str] = None,
    default_max_loops: Optional[int] = None,
    require_approval: Optional[bool] = None,
    timeout_seconds: Optional[int] = None,
) -> None:
    """Update the provided (non-``None``) fields of a project and bump ``updated_at``."""
    assignments: List[str] = []
    values: List[object] = []
    if repo_path is not None:
        assignments.append("repo_path = ?")
        values.append(repo_path)
    if default_provider is not None:
        assignments.append("default_provider = ?")
        values.append(default_provider)
    if default_max_loops is not None:
        assignments.append("default_max_loops = ?")
        values.append(int(default_max_loops))
    if require_approval is not None:
        assignments.append("require_approval = ?")
        values.append(1 if require_approval else 0)
    if timeout_seconds is not None:
        assignments.append("timeout_seconds = ?")
        values.append(int(timeout_seconds))
    assignments.append("updated_at = ?")
    values.append(_utcnow_iso())
    values.append(project_id)

    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        conn.execute(f"UPDATE projects SET {', '.join(assignments)} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def delete_project(db_path: str, project_id: int) -> None:
    """Delete a project profile. If it was the default, clear the default.

    Only the profile row is removed; no files on disk are touched.
    """
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
    finally:
        conn.close()
    if _get_setting(db_path, DEFAULT_PROJECT_KEY) == str(project_id):
        clear_default_project(db_path)


# -- default project (settings) ---------------------------------------------


def set_default_project(db_path: str, project_id: int) -> None:
    """Record ``project_id`` as the default project."""
    _set_setting(db_path, DEFAULT_PROJECT_KEY, str(int(project_id)))


def clear_default_project(db_path: str) -> None:
    """Clear the default project, if any."""
    _delete_setting(db_path, DEFAULT_PROJECT_KEY)


def get_default_project(db_path: str) -> Optional[Project]:
    """Return the default project, or ``None`` if unset or missing."""
    raw = _get_setting(db_path, DEFAULT_PROJECT_KEY)
    if raw is None:
        return None
    try:
        project_id = int(raw)
    except (TypeError, ValueError):
        return None
    return get_project_by_id(db_path, project_id)


def _get_setting(db_path: str, key: str) -> Optional[str]:
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row is not None else None
    finally:
        conn.close()


def _set_setting(db_path: str, key: str, value: str) -> None:
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def _delete_setting(db_path: str, key: str) -> None:
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


# -- runs --------------------------------------------------------------------


def create_run(
    db_path: str,
    root_prompt: str,
    provider: str,
    max_loops: int,
    require_approval: bool,
    project_id: Optional[int] = None,
    workspace: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> int:
    """Insert a new run in the CREATED state and return its id."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO runs "
            "(project_id, root_prompt, provider, status, max_loops, require_approval, "
            "created_at, finished_at, workspace, timeout_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                root_prompt,
                provider,
                RunStatus.CREATED.value,
                int(max_loops),
                1 if require_approval else 0,
                _utcnow_iso(),
                None,
                workspace,
                int(timeout_seconds) if timeout_seconds is not None else None,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def create_step(
    db_path: str,
    run_id: int,
    loop_index: int,
    prompt: str,
    status: str,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    exit_code: Optional[int] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    next_prompt: Optional[str] = None,
) -> int:
    """Insert a step belonging to ``run_id`` and return its id."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO steps "
            "(run_id, loop_index, prompt, stdout, stderr, exit_code, status, started_at, finished_at, next_prompt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, loop_index, prompt, stdout, stderr, exit_code, status, started_at, finished_at, next_prompt),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def update_run_status(
    db_path: str,
    run_id: int,
    status: str,
    finished_at: Optional[str] = None,
    validate: bool = True,
) -> None:
    """Update a run's status, enforcing the state machine by default.

    When ``validate`` is true the current status is read and
    ``validate_status_transition`` rejects illegal changes with a ``ValueError``. When
    the target is terminal, ``finished_at`` is set (using the supplied value or the
    current UTC time). Raises ``ValueError`` if the run does not exist.
    """
    path = _resolve_db_path(db_path)
    target = RunStatus(status)
    conn = _connect(path)
    try:
        row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"run {run_id} not found")
        if validate:
            validate_status_transition(row["status"], target)
        if target in TERMINAL_STATUSES:
            stamp = finished_at if finished_at is not None else _utcnow_iso()
            conn.execute(
                "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
                (target.value, stamp, run_id),
            )
        else:
            conn.execute("UPDATE runs SET status = ? WHERE id = ?", (target.value, run_id))
        conn.commit()
    finally:
        conn.close()


def list_runs(db_path: str, limit: int = 20) -> List[StoredRun]:
    """Return up to ``limit`` runs, newest first."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [_row_to_run(row) for row in rows]
    finally:
        conn.close()


def get_run(db_path: str, run_id: int) -> Optional[StoredRun]:
    """Return the run with ``run_id`` or ``None`` if it does not exist."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row is not None else None
    finally:
        conn.close()


def get_steps_for_run(db_path: str, run_id: int) -> List[StoredStep]:
    """Return all steps for ``run_id`` ordered by loop index, then id."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY loop_index ASC, id ASC",
            (run_id,),
        ).fetchall()
        return [_row_to_step(row) for row in rows]
    finally:
        conn.close()


# -- approvals ---------------------------------------------------------------


def create_approval(
    db_path: str,
    run_id: int,
    step_id: int,
    next_prompt: str,
    status: Optional[str] = None,
) -> int:
    """Insert an approval (PENDING by default) and return its id."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO approvals (run_id, step_id, next_prompt, status, created_at, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, step_id, next_prompt, status or ApprovalStatus.PENDING.value, _utcnow_iso(), None),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_pending_approval(db_path: str, run_id: int) -> Optional[Approval]:
    """Return the latest PENDING approval for ``run_id`` or ``None``."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM approvals WHERE run_id = ? AND status = ? ORDER BY id DESC LIMIT 1",
            (run_id, ApprovalStatus.PENDING.value),
        ).fetchone()
        return _row_to_approval(row) if row is not None else None
    finally:
        conn.close()


def approve_pending_approval(db_path: str, run_id: int) -> Optional[Approval]:
    """Mark the run's pending approval APPROVED. Return it, or ``None`` if absent."""
    return _decide_pending_approval(db_path, run_id, ApprovalStatus.APPROVED)


def reject_pending_approval(db_path: str, run_id: int) -> Optional[Approval]:
    """Mark the run's pending approval REJECTED. Return it, or ``None`` if absent."""
    return _decide_pending_approval(db_path, run_id, ApprovalStatus.REJECTED)


def _decide_pending_approval(db_path: str, run_id: int, status: ApprovalStatus) -> Optional[Approval]:
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM approvals WHERE run_id = ? AND status = ? ORDER BY id DESC LIMIT 1",
            (run_id, ApprovalStatus.PENDING.value),
        ).fetchone()
        if row is None:
            return None
        decided_at = _utcnow_iso()
        conn.execute(
            "UPDATE approvals SET status = ?, decided_at = ? WHERE id = ?",
            (status.value, decided_at, row["id"]),
        )
        conn.commit()
        approval = _row_to_approval(row)
        approval.status = status.value
        approval.decided_at = decided_at
        return approval
    finally:
        conn.close()


def list_approvals_for_run(db_path: str, run_id: int) -> List[Approval]:
    """Return all approvals for ``run_id`` ordered by id."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE run_id = ? ORDER BY id ASC", (run_id,)
        ).fetchall()
        return [_row_to_approval(row) for row in rows]
    finally:
        conn.close()


# -- artifacts ---------------------------------------------------------------


def create_artifact(
    db_path: str,
    run_id: int,
    artifact_type: str,
    content: Optional[str] = None,
    step_id: Optional[int] = None,
    path: Optional[str] = None,
) -> int:
    """Insert an artifact and return its id."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO artifacts (run_id, step_id, type, content, path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, step_id, artifact_type, content, path, _utcnow_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_artifacts_for_run(db_path: str, run_id: int, artifact_type: Optional[str] = None) -> List[Artifact]:
    """Return artifacts for ``run_id`` (optionally filtered by type), ordered by id."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        if artifact_type is None:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE run_id = ? ORDER BY id ASC", (run_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE run_id = ? AND type = ? ORDER BY id ASC",
                (run_id, artifact_type),
            ).fetchall()
        return [_row_to_artifact(row) for row in rows]
    finally:
        conn.close()


def list_artifacts_for_step(db_path: str, step_id: int) -> List[Artifact]:
    """Return artifacts for ``step_id`` ordered by id."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM artifacts WHERE step_id = ? ORDER BY id ASC", (step_id,)
        ).fetchall()
        return [_row_to_artifact(row) for row in rows]
    finally:
        conn.close()


def get_artifact(db_path: str, artifact_id: int) -> Optional[Artifact]:
    """Return the artifact with ``artifact_id`` or ``None``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return _row_to_artifact(row) if row is not None else None
    finally:
        conn.close()


def count_artifacts_by_type(db_path: str, run_id: int) -> Dict[str, int]:
    """Return a ``{type: count}`` map of a run's artifacts.

    Uses a ``GROUP BY`` aggregate so artifact *content* is never loaded -- only the type
    and a count -- which keeps run comparison cheap and avoids surfacing large output.
    """
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT type, COUNT(*) AS n FROM artifacts WHERE run_id = ? GROUP BY type ORDER BY type ASC",
            (run_id,),
        ).fetchall()
        return {row["type"]: int(row["n"]) for row in rows}
    finally:
        conn.close()


def count_artifacts_by_type_and_step(db_path: str, run_id: int) -> Dict[Optional[int], Dict[str, int]]:
    """Return a ``{step_id: {type: count}}`` map of a run's artifacts.

    Like :func:`count_artifacts_by_type` but grouped by step as well, so the prompt-chain
    view can show per-step artifact counts without loading any artifact *content*. Run-level
    artifacts (no step) are grouped under the ``None`` key.
    """
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT step_id, type, COUNT(*) AS n FROM artifacts WHERE run_id = ? "
            "GROUP BY step_id, type ORDER BY step_id ASC, type ASC",
            (run_id,),
        ).fetchall()
        result: Dict[Optional[int], Dict[str, int]] = {}
        for row in rows:
            result.setdefault(row["step_id"], {})[row["type"]] = int(row["n"])
        return result
    finally:
        conn.close()


# -- prompt templates --------------------------------------------------------


def _tags_to_text(tags: Optional[List[str]]) -> str:
    """Serialize a list of tags to a single comma-separated column value."""
    if not tags:
        return ""
    return ",".join(tag.strip() for tag in tags if tag and tag.strip())


def _tags_from_text(raw: Optional[str]) -> List[str]:
    """Parse a stored tags column back into a list of non-empty labels."""
    if not raw:
        return []
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


def create_template(
    db_path: str,
    name: str,
    body: str,
    description: str = "",
    tags: Optional[List[str]] = None,
) -> int:
    """Insert a prompt template and return its id. Raises on a duplicate name."""
    path = _resolve_db_path(db_path)
    now = _utcnow_iso()
    conn = _connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO templates (name, description, body, tags, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, description or "", body, _tags_to_text(tags), now, now),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_templates(db_path: str) -> List[Template]:
    """Return all prompt templates ordered by name."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        rows = conn.execute("SELECT * FROM templates ORDER BY name ASC").fetchall()
        return [_row_to_template(row) for row in rows]
    finally:
        conn.close()


def get_template_by_id(db_path: str, template_id: int) -> Optional[Template]:
    """Return the template with ``template_id`` or ``None``."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
        return _row_to_template(row) if row is not None else None
    finally:
        conn.close()


def get_template_by_name(db_path: str, name: str) -> Optional[Template]:
    """Return the template named ``name`` or ``None``."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        row = conn.execute("SELECT * FROM templates WHERE name = ?", (name,)).fetchone()
        return _row_to_template(row) if row is not None else None
    finally:
        conn.close()


def update_template(
    db_path: str,
    template_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    body: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> None:
    """Update the provided (non-``None``) fields of a template and bump ``updated_at``."""
    assignments: List[str] = []
    values: List[object] = []
    if name is not None:
        assignments.append("name = ?")
        values.append(name)
    if description is not None:
        assignments.append("description = ?")
        values.append(description)
    if body is not None:
        assignments.append("body = ?")
        values.append(body)
    if tags is not None:
        assignments.append("tags = ?")
        values.append(_tags_to_text(tags))
    assignments.append("updated_at = ?")
    values.append(_utcnow_iso())
    values.append(template_id)

    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        conn.execute(f"UPDATE templates SET {', '.join(assignments)} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def delete_template(db_path: str, template_id: int) -> None:
    """Delete a prompt template. Runs and other rows are not affected."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        conn.commit()
    finally:
        conn.close()


# -- worktree profiles -------------------------------------------------------


def create_worktree_record(
    db_path: str,
    project_id: int,
    name: str,
    branch: str,
    path: str,
    base_branch: Optional[str],
    status: str,
) -> int:
    """Insert a worktree profile and return its id. Raises on a duplicate name.

    This only records the worktree; the directory on disk is created/removed solely via
    ``git worktree`` commands in :mod:`autoprompt_runner.worktrees`.
    """
    db = _resolve_db_path(db_path)
    now = _utcnow_iso()
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO worktrees (project_id, name, branch, path, base_branch, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(project_id), name, branch, path, base_branch, status, now, now),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_worktrees(db_path: str) -> List[Worktree]:
    """Return all worktree profiles ordered by name."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute("SELECT * FROM worktrees ORDER BY name ASC").fetchall()
        return [_row_to_worktree(row) for row in rows]
    finally:
        conn.close()


def list_worktrees_for_project(db_path: str, project_id: int) -> List[Worktree]:
    """Return worktree profiles for ``project_id`` ordered by name."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM worktrees WHERE project_id = ? ORDER BY name ASC", (int(project_id),)
        ).fetchall()
        return [_row_to_worktree(row) for row in rows]
    finally:
        conn.close()


def get_worktree_by_id(db_path: str, worktree_id: int) -> Optional[Worktree]:
    """Return the worktree with ``worktree_id`` or ``None``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute("SELECT * FROM worktrees WHERE id = ?", (worktree_id,)).fetchone()
        return _row_to_worktree(row) if row is not None else None
    finally:
        conn.close()


def get_worktree_by_name(db_path: str, name: str) -> Optional[Worktree]:
    """Return the worktree named ``name`` or ``None``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute("SELECT * FROM worktrees WHERE name = ?", (name,)).fetchone()
        return _row_to_worktree(row) if row is not None else None
    finally:
        conn.close()


def update_worktree_status(db_path: str, worktree_id: int, status: str) -> None:
    """Update a worktree's status and bump ``updated_at``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute(
            "UPDATE worktrees SET status = ?, updated_at = ? WHERE id = ?",
            (status, _utcnow_iso(), worktree_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_worktree_record(db_path: str, worktree_id: int) -> None:
    """Delete a worktree profile row. No files on disk are touched here."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute("DELETE FROM worktrees WHERE id = ?", (worktree_id,))
        conn.commit()
    finally:
        conn.close()


def count_active_runs_for_workspace(db_path: str, workspace: Optional[str]) -> int:
    """Return how many non-terminal runs (RUNNING / WAITING_APPROVAL) target ``workspace``."""
    if not workspace:
        return 0
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM runs WHERE workspace = ? AND status IN (?, ?)",
            (workspace, RunStatus.RUNNING.value, RunStatus.WAITING_APPROVAL.value),
        ).fetchone()
        return int(row["c"]) if row is not None else 0
    finally:
        conn.close()


# -- workspace execution locks -----------------------------------------------


def create_run_lock(
    db_path: str,
    workspace_path: str,
    run_id: int,
    status: str = LOCK_ACTIVE,
    owner: Optional[str] = None,
    expires_at: Optional[str] = None,
) -> int:
    """Insert a run lock and return its id. ``workspace_path`` should be pre-normalized."""
    db = _resolve_db_path(db_path)
    now = _utcnow_iso()
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO run_locks (workspace_path, run_id, status, owner, created_at, updated_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (workspace_path, int(run_id), status, owner, now, now, expires_at),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_active_lock_for_workspace(db_path: str, workspace_path: str) -> Optional[RunLock]:
    """Return the current ACTIVE lock for ``workspace_path`` (pre-normalized) or ``None``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute(
            "SELECT * FROM run_locks WHERE workspace_path = ? AND status = ? ORDER BY id DESC LIMIT 1",
            (workspace_path, LOCK_ACTIVE),
        ).fetchone()
        return _row_to_run_lock(row) if row is not None else None
    finally:
        conn.close()


def release_run_lock(db_path: str, run_id: int) -> int:
    """Mark this run's ACTIVE lock(s) RELEASED. Returns how many rows were released."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        cur = conn.execute(
            "UPDATE run_locks SET status = ?, updated_at = ? WHERE run_id = ? AND status = ?",
            (LOCK_RELEASED, _utcnow_iso(), int(run_id), LOCK_ACTIVE),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def expire_old_locks(db_path: str, now: str) -> int:
    """Mark ACTIVE locks whose ``expires_at`` is before ``now`` as EXPIRED.

    ``now`` is an ISO 8601 string (timezone-aware). Returns how many were expired.
    """
    db = _resolve_db_path(db_path)
    now_dt = _parse_iso(now)
    if now_dt is None:
        return 0
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT id, expires_at FROM run_locks WHERE status = ? AND expires_at IS NOT NULL",
            (LOCK_ACTIVE,),
        ).fetchall()
        expired = [row["id"] for row in rows
                   if (_parse_iso(row["expires_at"]) is not None and _parse_iso(row["expires_at"]) < now_dt)]
        if expired:
            stamp = _utcnow_iso()
            conn.executemany(
                "UPDATE run_locks SET status = ?, updated_at = ? WHERE id = ?",
                [(LOCK_EXPIRED, stamp, lock_id) for lock_id in expired],
            )
            conn.commit()
        return len(expired)
    finally:
        conn.close()


def list_locks(db_path: str, limit: int = 50) -> List[RunLock]:
    """Return up to ``limit`` locks, newest first."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute("SELECT * FROM run_locks ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [_row_to_run_lock(row) for row in rows]
    finally:
        conn.close()


def get_lock_for_run(db_path: str, run_id: int) -> Optional[RunLock]:
    """Return the most recent lock for ``run_id`` or ``None``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute(
            "SELECT * FROM run_locks WHERE run_id = ? ORDER BY id DESC LIMIT 1", (int(run_id),)
        ).fetchone()
        return _row_to_run_lock(row) if row is not None else None
    finally:
        conn.close()


# -- run queue ---------------------------------------------------------------


def enqueue_run(db_path: str, run_id: int, priority: int = 100, max_attempts: int = 1) -> int:
    """Insert a QUEUED job for ``run_id`` and return its id.

    Raises ``ValueError`` if the run already has an active (QUEUED/RUNNING) job, so a run
    can never have multiple active queue jobs.
    """
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        existing = conn.execute(
            "SELECT id FROM run_queue WHERE run_id = ? AND status IN (?, ?)",
            (int(run_id), QUEUE_QUEUED, QUEUE_RUNNING),
        ).fetchone()
        if existing is not None:
            raise ValueError(f"run {run_id} already has an active queue job")
        cur = conn.execute(
            "INSERT INTO run_queue (run_id, status, priority, attempts, max_attempts, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (int(run_id), QUEUE_QUEUED, int(priority), 0, int(max_attempts), _utcnow_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_next_queued_job(db_path: str) -> Optional[QueueJob]:
    """Return the next job to run: lowest priority number, then oldest, or ``None``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute(
            "SELECT * FROM run_queue WHERE status = ? ORDER BY priority ASC, created_at ASC, id ASC LIMIT 1",
            (QUEUE_QUEUED,),
        ).fetchone()
        return _row_to_queue_job(row) if row is not None else None
    finally:
        conn.close()


def mark_job_running(db_path: str, job_id: int) -> None:
    """Mark a job RUNNING, stamp ``started_at``, and increment ``attempts``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute(
            "UPDATE run_queue SET status = ?, started_at = ?, attempts = attempts + 1 WHERE id = ?",
            (QUEUE_RUNNING, _utcnow_iso(), int(job_id)),
        )
        conn.commit()
    finally:
        conn.close()


def mark_job_done(db_path: str, job_id: int) -> None:
    """Mark a job DONE and stamp ``finished_at``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute(
            "UPDATE run_queue SET status = ?, finished_at = ? WHERE id = ?",
            (QUEUE_DONE, _utcnow_iso(), int(job_id)),
        )
        conn.commit()
    finally:
        conn.close()


def mark_job_failed(db_path: str, job_id: int, last_error: Optional[str] = None) -> None:
    """Mark a job FAILED, stamp ``finished_at``, and record ``last_error``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute(
            "UPDATE run_queue SET status = ?, finished_at = ?, last_error = ? WHERE id = ?",
            (QUEUE_FAILED, _utcnow_iso(), last_error, int(job_id)),
        )
        conn.commit()
    finally:
        conn.close()


def cancel_job(db_path: str, run_id: int) -> int:
    """Cancel the QUEUED job for ``run_id`` (RUNNING jobs are left untouched).

    Returns how many jobs were cancelled (0 when there is no queued job for the run).
    """
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        cur = conn.execute(
            "UPDATE run_queue SET status = ?, finished_at = ? WHERE run_id = ? AND status = ?",
            (QUEUE_CANCELLED, _utcnow_iso(), int(run_id), QUEUE_QUEUED),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_job_by_run_id(db_path: str, run_id: int) -> Optional[QueueJob]:
    """Return the most recent queue job for ``run_id`` or ``None``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute(
            "SELECT * FROM run_queue WHERE run_id = ? ORDER BY id DESC LIMIT 1", (int(run_id),)
        ).fetchone()
        return _row_to_queue_job(row) if row is not None else None
    finally:
        conn.close()


def list_queue(db_path: str, limit: int = 50) -> List[QueueJob]:
    """Return up to ``limit`` queue jobs, newest first."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute("SELECT * FROM run_queue ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [_row_to_queue_job(row) for row in rows]
    finally:
        conn.close()


# -- run cancellations -------------------------------------------------------


def request_run_cancellation(db_path: str, run_id: int, reason: Optional[str] = None) -> int:
    """Insert a REQUESTED cancellation for ``run_id`` and return its id."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO run_cancellations (run_id, status, reason, requested_at) VALUES (?, ?, ?, ?)",
            (int(run_id), CANCELLATION_REQUESTED, reason, _utcnow_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_cancellation_for_run(db_path: str, run_id: int) -> Optional[RunCancellation]:
    """Return the most recent cancellation for ``run_id`` or ``None``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute(
            "SELECT * FROM run_cancellations WHERE run_id = ? ORDER BY id DESC LIMIT 1", (int(run_id),)
        ).fetchone()
        return _row_to_cancellation(row) if row is not None else None
    finally:
        conn.close()


def complete_run_cancellation(db_path: str, cancellation_id: int) -> None:
    """Mark a cancellation COMPLETED and stamp ``completed_at``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute(
            "UPDATE run_cancellations SET status = ?, completed_at = ? WHERE id = ?",
            (CANCELLATION_COMPLETED, _utcnow_iso(), int(cancellation_id)),
        )
        conn.commit()
    finally:
        conn.close()


def fail_run_cancellation(db_path: str, cancellation_id: int, error: Optional[str] = None) -> None:
    """Mark a cancellation FAILED, stamp ``completed_at``, and record ``error``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute(
            "UPDATE run_cancellations SET status = ?, completed_at = ?, error = ? WHERE id = ?",
            (CANCELLATION_FAILED, _utcnow_iso(), error, int(cancellation_id)),
        )
        conn.commit()
    finally:
        conn.close()


def list_cancellations(db_path: str, limit: int = 50) -> List[RunCancellation]:
    """Return up to ``limit`` cancellations, newest first."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM run_cancellations ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [_row_to_cancellation(row) for row in rows]
    finally:
        conn.close()


# -- provider profiles -------------------------------------------------------

# Sentinel so update_provider_profile can distinguish "leave default_args unchanged"
# from "set default_args to NULL".
_UNSET = object()


def create_provider_profile(
    db_path: str,
    name: str,
    type: str,
    command: str,
    default_timeout_seconds: int,
    default_args: Optional[str] = None,
    enabled: bool = True,
) -> int:
    """Insert a provider profile and return its id. ``name`` must be unique."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        now = _utcnow_iso()
        cur = conn.execute(
            "INSERT INTO provider_profiles "
            "(name, type, command, default_timeout_seconds, default_args, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, type, command, int(default_timeout_seconds), default_args, 1 if enabled else 0, now, now),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_provider_profiles(db_path: str) -> List[ProviderProfile]:
    """Return all provider profiles ordered by name."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute("SELECT * FROM provider_profiles ORDER BY name ASC").fetchall()
        return [_row_to_provider_profile(row) for row in rows]
    finally:
        conn.close()


def get_provider_profile_by_id(db_path: str, profile_id: int) -> Optional[ProviderProfile]:
    """Return the provider profile with ``profile_id`` or ``None``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute("SELECT * FROM provider_profiles WHERE id = ?", (profile_id,)).fetchone()
        return _row_to_provider_profile(row) if row is not None else None
    finally:
        conn.close()


def get_provider_profile_by_name(db_path: str, name: str) -> Optional[ProviderProfile]:
    """Return the provider profile named ``name`` or ``None``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute("SELECT * FROM provider_profiles WHERE name = ?", (name,)).fetchone()
        return _row_to_provider_profile(row) if row is not None else None
    finally:
        conn.close()


def update_provider_profile(
    db_path: str,
    profile_id: int,
    *,
    type: Optional[str] = None,
    command: Optional[str] = None,
    default_timeout_seconds: Optional[int] = None,
    default_args=_UNSET,
    enabled: Optional[bool] = None,
) -> None:
    """Update the editable fields of a provider profile (only the ones provided)."""
    sets: List[str] = []
    params: List = []
    if type is not None:
        sets.append("type = ?")
        params.append(type)
    if command is not None:
        sets.append("command = ?")
        params.append(command)
    if default_timeout_seconds is not None:
        sets.append("default_timeout_seconds = ?")
        params.append(int(default_timeout_seconds))
    if default_args is not _UNSET:
        sets.append("default_args = ?")
        params.append(default_args)
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(1 if enabled else 0)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(_utcnow_iso())
    params.append(profile_id)
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute(f"UPDATE provider_profiles SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def set_provider_enabled(db_path: str, profile_id: int, enabled: bool) -> None:
    """Enable or disable a provider profile."""
    update_provider_profile(db_path, profile_id, enabled=enabled)


def delete_provider_profile(db_path: str, profile_id: int) -> None:
    """Delete a provider profile (does not affect any external CLI tool)."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute("DELETE FROM provider_profiles WHERE id = ?", (profile_id,))
        conn.commit()
    finally:
        conn.close()


def seed_default_provider_profiles(db_path: str, specs, force: bool = False) -> dict:
    """Create the given default provider profiles if missing.

    Each spec is a dict with ``name``/``type``/``command``/``default_timeout_seconds`` and
    optional ``default_args``/``enabled``. An existing profile is left untouched (a
    user-modified profile is never overwritten) unless ``force`` is set, in which case it is
    reset to the spec values. Returns ``{"seeded", "skipped", "total"}`` counts.
    """
    seeded = 0
    skipped = 0
    for spec in specs:
        existing = get_provider_profile_by_name(db_path, spec["name"])
        if existing is None:
            create_provider_profile(
                db_path,
                name=spec["name"],
                type=spec["type"],
                command=spec["command"],
                default_timeout_seconds=spec["default_timeout_seconds"],
                default_args=spec.get("default_args"),
                enabled=spec.get("enabled", True),
            )
            seeded += 1
        elif force:
            update_provider_profile(
                db_path,
                existing.id,
                type=spec["type"],
                command=spec["command"],
                default_timeout_seconds=spec["default_timeout_seconds"],
                default_args=spec.get("default_args"),
                enabled=spec.get("enabled", True),
            )
            skipped += 1
        else:
            skipped += 1
    return {"seeded": seeded, "skipped": skipped, "total": len(specs)}


# -- recovery attempts -------------------------------------------------------


def create_recovery_attempt(
    db_path: str,
    source_run_id: int,
    recovery_prompt: str,
    failed_step_id: Optional[int] = None,
    reason: Optional[str] = None,
    status: str = "PROPOSED",
) -> int:
    """Insert a recovery attempt (PROPOSED by default) and return its id."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO recovery_attempts "
            "(source_run_id, recovery_run_id, failed_step_id, status, recovery_prompt, reason, "
            "created_at, decided_at, executed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source_run_id, None, failed_step_id, status, recovery_prompt, reason, _utcnow_iso(), None, None),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_recovery_attempt(db_path: str, recovery_id: int) -> Optional[RecoveryAttempt]:
    """Return the recovery attempt with ``recovery_id`` or ``None``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute("SELECT * FROM recovery_attempts WHERE id = ?", (recovery_id,)).fetchone()
        return _row_to_recovery_attempt(row) if row is not None else None
    finally:
        conn.close()


def get_latest_recovery_for_run(db_path: str, run_id: int) -> Optional[RecoveryAttempt]:
    """Return the most recent recovery attempt for ``run_id`` (highest id), or ``None``."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute(
            "SELECT * FROM recovery_attempts WHERE source_run_id = ? ORDER BY id DESC LIMIT 1", (run_id,)
        ).fetchone()
        return _row_to_recovery_attempt(row) if row is not None else None
    finally:
        conn.close()


def list_recoveries_for_run(db_path: str, run_id: int) -> List[RecoveryAttempt]:
    """Return all recovery attempts for ``run_id`` ordered by id."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM recovery_attempts WHERE source_run_id = ? ORDER BY id ASC", (run_id,)
        ).fetchall()
        return [_row_to_recovery_attempt(row) for row in rows]
    finally:
        conn.close()


def update_recovery_status(
    db_path: str,
    recovery_id: int,
    status: str,
    decided_at=_UNSET,
    executed_at=_UNSET,
    reason=_UNSET,
) -> None:
    """Update a recovery attempt's status (and optionally decided/executed timestamps, reason)."""
    sets = ["status = ?"]
    params: List = [status]
    if decided_at is not _UNSET:
        sets.append("decided_at = ?")
        params.append(decided_at)
    if executed_at is not _UNSET:
        sets.append("executed_at = ?")
        params.append(executed_at)
    if reason is not _UNSET:
        sets.append("reason = ?")
        params.append(reason)
    params.append(recovery_id)
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute(f"UPDATE recovery_attempts SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def attach_recovery_run(db_path: str, recovery_id: int, recovery_run_id: int) -> None:
    """Link the created recovery run to its recovery attempt."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute(
            "UPDATE recovery_attempts SET recovery_run_id = ? WHERE id = ?", (recovery_run_id, recovery_id)
        )
        conn.commit()
    finally:
        conn.close()


def list_recoveries(db_path: str, limit: int = 50) -> List[RecoveryAttempt]:
    """Return up to ``limit`` recovery attempts, newest first."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM recovery_attempts ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [_row_to_recovery_attempt(row) for row in rows]
    finally:
        conn.close()


# -- export / import ---------------------------------------------------------

# Tables that may be exported/imported (whitelisted so a table name is never interpolated
# from untrusted input).
_EXPORTABLE_TABLES = (
    "projects",
    "provider_profiles",
    "templates",
    "runs",
    "steps",
    "approvals",
    "artifacts",
    "recovery_attempts",
)


def export_table_rows(db_path: str, table: str) -> List[dict]:
    """Return every row of an exportable ``table`` as a list of plain dicts (column -> value)."""
    if table not in _EXPORTABLE_TABLES:
        raise ValueError(f"table is not exportable: {table}")
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY id ASC").fetchall()  # noqa: S608 (whitelisted)
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _insert_returning_id(db_path: str, sql: str, params) -> int:
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def insert_imported_project(
    db_path: str, *, name, repo_path, default_provider, default_max_loops,
    require_approval, timeout_seconds, created_at, updated_at,
) -> int:
    """Insert an imported project row (new local id) preserving its stored fields."""
    return _insert_returning_id(
        db_path,
        "INSERT INTO projects (name, repo_path, default_provider, default_max_loops, "
        "require_approval, timeout_seconds, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, repo_path, default_provider, default_max_loops, require_approval, timeout_seconds,
         created_at or _utcnow_iso(), updated_at),
    )


def insert_imported_provider_profile(
    db_path: str, *, name, type, command, default_timeout_seconds, default_args, enabled, created_at, updated_at,
) -> int:
    """Insert an imported provider profile row (new local id)."""
    return _insert_returning_id(
        db_path,
        "INSERT INTO provider_profiles (name, type, command, default_timeout_seconds, default_args, "
        "enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, type, command, default_timeout_seconds, default_args, enabled,
         created_at or _utcnow_iso(), updated_at or _utcnow_iso()),
    )


def insert_imported_template(db_path: str, *, name, description, body, tags, created_at, updated_at) -> int:
    """Insert an imported template row (new local id). ``tags`` is the stored text form."""
    return _insert_returning_id(
        db_path,
        "INSERT INTO templates (name, description, body, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, description, body, tags, created_at or _utcnow_iso(), updated_at),
    )


def insert_imported_run(
    db_path: str, *, project_id, root_prompt, provider, status, max_loops,
    require_approval, created_at, finished_at, workspace, timeout_seconds,
) -> int:
    """Insert an imported run row (new local id), preserving status/timestamps."""
    return _insert_returning_id(
        db_path,
        "INSERT INTO runs (project_id, root_prompt, provider, status, max_loops, require_approval, "
        "created_at, finished_at, workspace, timeout_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, root_prompt, provider, status, max_loops, require_approval,
         created_at or _utcnow_iso(), finished_at, workspace, timeout_seconds),
    )


def insert_imported_step(
    db_path: str, *, run_id, loop_index, prompt, stdout, stderr, exit_code, status, started_at, finished_at, next_prompt,
) -> int:
    """Insert an imported step row (new local id) under a remapped ``run_id``."""
    return _insert_returning_id(
        db_path,
        "INSERT INTO steps (run_id, loop_index, prompt, stdout, stderr, exit_code, status, "
        "started_at, finished_at, next_prompt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, loop_index, prompt, stdout, stderr, exit_code, status, started_at, finished_at, next_prompt),
    )


def insert_imported_approval(db_path: str, *, run_id, step_id, next_prompt, status, created_at, decided_at) -> int:
    """Insert an imported approval row (new local id) under remapped ``run_id``/``step_id``."""
    return _insert_returning_id(
        db_path,
        "INSERT INTO approvals (run_id, step_id, next_prompt, status, created_at, decided_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, step_id, next_prompt, status, created_at or _utcnow_iso(), decided_at),
    )


def insert_imported_artifact(db_path: str, *, run_id, step_id, type, content, path, created_at) -> int:
    """Insert an imported artifact row (new local id) under remapped ``run_id``/``step_id``."""
    return _insert_returning_id(
        db_path,
        "INSERT INTO artifacts (run_id, step_id, type, content, path, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, step_id, type, content, path, created_at or _utcnow_iso()),
    )


def insert_imported_recovery(
    db_path: str, *, source_run_id, recovery_run_id, failed_step_id, status, recovery_prompt,
    reason, created_at, decided_at, executed_at,
) -> int:
    """Insert an imported recovery attempt (new local id) under remapped run/step ids."""
    return _insert_returning_id(
        db_path,
        "INSERT INTO recovery_attempts (source_run_id, recovery_run_id, failed_step_id, status, "
        "recovery_prompt, reason, created_at, decided_at, executed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (source_run_id, recovery_run_id, failed_step_id, status, recovery_prompt, reason,
         created_at or _utcnow_iso(), decided_at, executed_at),
    )


# -- run events (live log / SSE) ---------------------------------------------


def create_run_event(
    db_path: str,
    run_id: int,
    type: str,
    message: Optional[str] = None,
    payload: Optional[str] = None,
    step_id: Optional[int] = None,
):
    """Insert a run event (``payload`` is a pre-serialized JSON string). Returns the RunEvent."""
    from . import events as _events  # local import avoids a module-load cycle

    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        now = _utcnow_iso()
        cur = conn.execute(
            "INSERT INTO run_events (run_id, step_id, type, message, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, step_id, type, message, payload, now),
        )
        conn.commit()
        return _events.RunEvent(
            id=int(cur.lastrowid), run_id=run_id, step_id=step_id, type=type,
            message=message, payload=payload, created_at=now,
        )
    finally:
        conn.close()


def list_run_events(db_path: str, run_id: int, after_id: Optional[int] = None, limit: int = 100):
    """Return a run's events (id ascending); only those with ``id > after_id`` when given."""
    from . import events as _events

    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        if after_id is None:
            rows = conn.execute(
                "SELECT * FROM run_events WHERE run_id = ? ORDER BY id ASC LIMIT ?", (run_id, int(limit))
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM run_events WHERE run_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
                (run_id, int(after_id), int(limit)),
            ).fetchall()
        return [
            _events.RunEvent(
                id=r["id"], run_id=r["run_id"], step_id=_opt(r, "step_id"), type=r["type"],
                message=_opt(r, "message"), payload=_opt(r, "payload"), created_at=r["created_at"],
            )
            for r in rows
        ]
    finally:
        conn.close()


def get_latest_run_event(db_path: str, run_id: int):
    """Return the most recent event for ``run_id`` (highest id), or ``None``."""
    from . import events as _events

    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        row = conn.execute(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY id DESC LIMIT 1", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return _events.RunEvent(
            id=row["id"], run_id=row["run_id"], step_id=_opt(row, "step_id"), type=row["type"],
            message=_opt(row, "message"), payload=_opt(row, "payload"), created_at=row["created_at"],
        )
    finally:
        conn.close()


def delete_old_run_events(db_path: str, run_id: int, keep_last: int = 1000) -> int:
    """Delete all but the most recent ``keep_last`` events for ``run_id``. Returns rows deleted."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        cur = conn.execute(
            "DELETE FROM run_events WHERE run_id = ? AND id NOT IN "
            "(SELECT id FROM run_events WHERE run_id = ? ORDER BY id DESC LIMIT ?)",
            (run_id, run_id, int(keep_last)),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# -- worker heartbeats + reconciliation queries ------------------------------


def create_worker_heartbeat(db_path: str, worker_id: str, status: str = WORKER_ACTIVE) -> int:
    """Insert a worker heartbeat (ACTIVE by default) and return its id."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        now = _utcnow_iso()
        cur = conn.execute(
            "INSERT INTO worker_heartbeats (worker_id, status, started_at, updated_at, stopped_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (worker_id, status, now, now, None),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def update_worker_heartbeat(db_path: str, heartbeat_id: int) -> None:
    """Refresh a heartbeat's ``updated_at`` to now (called each worker poll cycle)."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute("UPDATE worker_heartbeats SET updated_at = ? WHERE id = ?", (_utcnow_iso(), heartbeat_id))
        conn.commit()
    finally:
        conn.close()


def stop_worker_heartbeat(db_path: str, heartbeat_id: int) -> None:
    """Mark a heartbeat STOPPED (clean worker shutdown)."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        now = _utcnow_iso()
        conn.execute(
            "UPDATE worker_heartbeats SET status = ?, stopped_at = ?, updated_at = ? WHERE id = ?",
            (WORKER_STOPPED, now, now, heartbeat_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_worker_heartbeats(db_path: str, limit: int = 50) -> List[WorkerHeartbeat]:
    """Return recent worker heartbeats, newest first."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM worker_heartbeats ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [_row_to_worker_heartbeat(row) for row in rows]
    finally:
        conn.close()


def get_active_worker_heartbeats(db_path: str) -> List[WorkerHeartbeat]:
    """Return all heartbeats currently marked ACTIVE (may include stale ones)."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM worker_heartbeats WHERE status = ? ORDER BY id DESC", (WORKER_ACTIVE,)
        ).fetchall()
        return [_row_to_worker_heartbeat(row) for row in rows]
    finally:
        conn.close()


def detect_stale_worker_heartbeats(db_path: str, before_iso: str) -> List[WorkerHeartbeat]:
    """Return ACTIVE heartbeats whose ``updated_at`` is older than ``before_iso`` (stale)."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM worker_heartbeats WHERE status = ? AND updated_at < ? ORDER BY id DESC",
            (WORKER_ACTIVE, before_iso),
        ).fetchall()
        return [_row_to_worker_heartbeat(row) for row in rows]
    finally:
        conn.close()


def list_runs_by_status(db_path: str, status: str) -> List[StoredRun]:
    """Return all runs with the given status (used by reconciliation)."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute("SELECT * FROM runs WHERE status = ? ORDER BY id ASC", (status,)).fetchall()
        return [_row_to_run(row) for row in rows]
    finally:
        conn.close()


def list_queue_jobs_by_status(db_path: str, status: str) -> List[QueueJob]:
    """Return all queue jobs with the given status (used by reconciliation)."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute("SELECT * FROM run_queue WHERE status = ? ORDER BY id ASC", (status,)).fetchall()
        return [_row_to_queue_job(row) for row in rows]
    finally:
        conn.close()


def list_active_locks(db_path: str) -> List[RunLock]:
    """Return all locks currently marked ACTIVE (used by reconciliation)."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM run_locks WHERE status = ? ORDER BY id ASC", (LOCK_ACTIVE,)
        ).fetchall()
        return [_row_to_run_lock(row) for row in rows]
    finally:
        conn.close()


def list_cancellations_by_status(db_path: str, status: str) -> List[RunCancellation]:
    """Return all run cancellations with the given status (used by reconciliation)."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM run_cancellations WHERE status = ? ORDER BY id ASC", (status,)
        ).fetchall()
        return [_row_to_cancellation(row) for row in rows]
    finally:
        conn.close()


def expire_lock_by_id(db_path: str, lock_id: int) -> None:
    """Mark a single lock EXPIRED by id (used when its run is terminal)."""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        conn.execute(
            "UPDATE run_locks SET status = ?, updated_at = ? WHERE id = ?",
            (LOCK_EXPIRED, _utcnow_iso(), lock_id),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_worker_heartbeat(row: sqlite3.Row) -> WorkerHeartbeat:
    return WorkerHeartbeat(
        id=row["id"],
        worker_id=row["worker_id"],
        status=row["status"],
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        stopped_at=_opt(row, "stopped_at"),
    )


# -- search (SQLite LIKE; no external engine, no disk reads) ------------------


def _like_pattern(query: str) -> str:
    """Escape LIKE wildcards in ``query`` and wrap it as a ``%contains%`` pattern."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_runs(
    db_path: str,
    query: Optional[str] = None,
    status: Optional[str] = None,
    provider: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[StoredRun]:
    """Return runs matching the LIKE query / filters, newest first."""
    clauses: List[str] = []
    params: List[object] = []
    if query:
        like = _like_pattern(query)
        clauses.append("(root_prompt LIKE ? ESCAPE '\\' OR provider LIKE ? ESCAPE '\\' OR status LIKE ? ESCAPE '\\')")
        params += [like, like, like]
    if status:
        clauses.append("status = ?")
        params.append(status)
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if date_from:
        clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("created_at <= ?")
        params.append(date_to)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            f"SELECT * FROM runs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*params, int(limit), int(offset)],
        ).fetchall()
        return [_row_to_run(row) for row in rows]
    finally:
        conn.close()


def search_steps(
    db_path: str,
    query: Optional[str] = None,
    run_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[StoredStep]:
    """Return steps whose prompt / stdout / stderr / next_prompt match the LIKE query."""
    clauses: List[str] = []
    params: List[object] = []
    if query:
        like = _like_pattern(query)
        clauses.append(
            "(prompt LIKE ? ESCAPE '\\' OR stdout LIKE ? ESCAPE '\\' "
            "OR stderr LIKE ? ESCAPE '\\' OR next_prompt LIKE ? ESCAPE '\\')"
        )
        params += [like, like, like, like]
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(int(run_id))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            f"SELECT * FROM steps {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*params, int(limit), int(offset)],
        ).fetchall()
        return [_row_to_step(row) for row in rows]
    finally:
        conn.close()


def search_artifacts(
    db_path: str,
    query: Optional[str] = None,
    artifact_type: Optional[str] = None,
    run_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Artifact]:
    """Return artifacts whose type / content / path match the LIKE query / filters."""
    clauses: List[str] = []
    params: List[object] = []
    if query:
        like = _like_pattern(query)
        clauses.append("(type LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\')")
        params += [like, like, like]
    if artifact_type:
        clauses.append("type = ?")
        params.append(artifact_type)
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(int(run_id))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    db = _resolve_db_path(db_path)
    conn = _connect(db)
    try:
        rows = conn.execute(
            f"SELECT * FROM artifacts {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*params, int(limit), int(offset)],
        ).fetchall()
        return [_row_to_artifact(row) for row in rows]
    finally:
        conn.close()


# -- run logs (for polling) --------------------------------------------------

_LOG_TAIL_LIMIT = 4000


def _log_tail(text: Optional[str]) -> str:
    """Return ``text``, or its last ``_LOG_TAIL_LIMIT`` chars, for a compact log view."""
    if not text:
        return ""
    if len(text) <= _LOG_TAIL_LIMIT:
        return text
    return "...[truncated; showing the last 4000 characters]...\n" + text[-_LOG_TAIL_LIMIT:]


def get_latest_step_for_run(db_path: str, run_id: int) -> Optional[StoredStep]:
    """Return the most recent step for ``run_id`` (highest loop index), or ``None``."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY loop_index DESC, id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return _row_to_step(row) if row is not None else None
    finally:
        conn.close()


def get_latest_artifact_by_type(db_path: str, run_id: int, artifact_type: str) -> Optional[Artifact]:
    """Return the most recent artifact of ``artifact_type`` for ``run_id``, or ``None``."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE run_id = ? AND type = ? ORDER BY id DESC LIMIT 1",
            (run_id, artifact_type),
        ).fetchone()
        return _row_to_artifact(row) if row is not None else None
    finally:
        conn.close()


def get_run_logs(db_path: str, run_id: int) -> Optional[dict]:
    """Assemble a compact logs snapshot for a run, or ``None`` if the run is missing.

    Combines the run status, the latest step, and the latest runner stdout/stderr
    artifacts into the shape served by ``GET /runs/{id}/logs``. Read-only: it does not
    change runner execution (stdout/stderr are captured after each step completes).
    """
    run = get_run(db_path, run_id)
    if run is None:
        return None
    latest_step = get_latest_step_for_run(db_path, run_id)
    stdout_artifact = get_latest_artifact_by_type(db_path, run_id, "runner_stdout")
    stderr_artifact = get_latest_artifact_by_type(db_path, run_id, "runner_stderr")
    stdout = stdout_artifact.content if stdout_artifact is not None else (latest_step.stdout if latest_step else None)
    stderr = stderr_artifact.content if stderr_artifact is not None else (latest_step.stderr if latest_step else None)
    return {
        "run_id": run.id,
        "status": run.status,
        "generated_at": _utcnow_iso(),
        "latest_step_id": latest_step.id if latest_step is not None else None,
        "stdout": _log_tail(stdout),
        "stderr": _log_tail(stderr),
        "stdout_artifact_id": stdout_artifact.id if stdout_artifact is not None else None,
        "stderr_artifact_id": stderr_artifact.id if stderr_artifact is not None else None,
    }


# -- row mappers -------------------------------------------------------------


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        repo_path=row["repo_path"],
        default_provider=_opt(row, "default_provider"),
        default_max_loops=_opt(row, "default_max_loops"),
        require_approval=bool(_opt(row, "require_approval")),
        timeout_seconds=_opt(row, "timeout_seconds"),
        created_at=row["created_at"],
        updated_at=_opt(row, "updated_at"),
    )


def _row_to_run(row: sqlite3.Row) -> StoredRun:
    return StoredRun(
        id=row["id"],
        project_id=row["project_id"],
        root_prompt=row["root_prompt"],
        provider=row["provider"],
        status=row["status"],
        max_loops=row["max_loops"],
        require_approval=bool(row["require_approval"]),
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        workspace=_opt(row, "workspace"),
        timeout_seconds=_opt(row, "timeout_seconds"),
    )


def _row_to_step(row: sqlite3.Row) -> StoredStep:
    return StoredStep(
        id=row["id"],
        run_id=row["run_id"],
        loop_index=row["loop_index"],
        prompt=row["prompt"],
        status=row["status"],
        stdout=row["stdout"],
        stderr=row["stderr"],
        exit_code=row["exit_code"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        next_prompt=row["next_prompt"],
    )


def _row_to_approval(row: sqlite3.Row) -> Approval:
    return Approval(
        id=row["id"],
        run_id=row["run_id"],
        step_id=row["step_id"],
        next_prompt=row["next_prompt"],
        status=row["status"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
    )


def _row_to_artifact(row: sqlite3.Row) -> Artifact:
    return Artifact(
        id=row["id"],
        run_id=row["run_id"],
        step_id=row["step_id"],
        type=row["type"],
        content=row["content"],
        path=row["path"],
        created_at=row["created_at"],
    )


def _row_to_template(row: sqlite3.Row) -> Template:
    return Template(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        body=row["body"],
        tags=_tags_from_text(_opt(row, "tags")),
        created_at=row["created_at"],
        updated_at=_opt(row, "updated_at"),
    )


def _row_to_worktree(row: sqlite3.Row) -> Worktree:
    return Worktree(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        branch=row["branch"],
        path=row["path"],
        base_branch=_opt(row, "base_branch"),
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_run_lock(row: sqlite3.Row) -> RunLock:
    return RunLock(
        id=row["id"],
        workspace_path=row["workspace_path"],
        run_id=row["run_id"],
        status=row["status"],
        owner=_opt(row, "owner"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=_opt(row, "expires_at"),
    )


def _row_to_queue_job(row: sqlite3.Row) -> QueueJob:
    return QueueJob(
        id=row["id"],
        run_id=row["run_id"],
        status=row["status"],
        priority=row["priority"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        created_at=row["created_at"],
        started_at=_opt(row, "started_at"),
        finished_at=_opt(row, "finished_at"),
        last_error=_opt(row, "last_error"),
    )


def _row_to_cancellation(row: sqlite3.Row) -> RunCancellation:
    return RunCancellation(
        id=row["id"],
        run_id=row["run_id"],
        status=row["status"],
        reason=_opt(row, "reason"),
        requested_at=row["requested_at"],
        completed_at=_opt(row, "completed_at"),
        error=_opt(row, "error"),
    )


def _row_to_provider_profile(row: sqlite3.Row) -> ProviderProfile:
    return ProviderProfile(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        command=row["command"],
        default_timeout_seconds=row["default_timeout_seconds"],
        default_args=_opt(row, "default_args"),
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_recovery_attempt(row: sqlite3.Row) -> RecoveryAttempt:
    return RecoveryAttempt(
        id=row["id"],
        source_run_id=row["source_run_id"],
        recovery_run_id=_opt(row, "recovery_run_id"),
        failed_step_id=_opt(row, "failed_step_id"),
        status=row["status"],
        recovery_prompt=row["recovery_prompt"],
        reason=_opt(row, "reason"),
        created_at=row["created_at"],
        decided_at=_opt(row, "decided_at"),
        executed_at=_opt(row, "executed_at"),
    )
