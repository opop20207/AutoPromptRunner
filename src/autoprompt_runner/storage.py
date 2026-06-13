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
from typing import List, Optional

from .approvals import ApprovalStatus
from .models import Approval, Artifact, Project, StoredRun, StoredStep
from .state import RunStatus, TERMINAL_STATUSES, validate_status_transition

# Default database location, relative to the current working directory.
DEFAULT_DB_PATH = os.path.join(".autoprompt", "autoprompt.db")

# Settings key that stores the default project's id.
DEFAULT_PROJECT_KEY = "default_project_id"

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


def _resolve_db_path(db_path: Optional[str]) -> str:
    return db_path or DEFAULT_DB_PATH


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
