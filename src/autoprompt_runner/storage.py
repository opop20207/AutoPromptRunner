"""SQLite persistence for AutoPromptRunner.

A thin data-access layer over the Python standard-library ``sqlite3`` module. It
stores projects, runs, steps, and approvals so run history survives across CLI
invocations. All state stays on the local machine (see AGENTS.md, "Logging Rules").
No third-party packages are used.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from .approvals import ApprovalStatus
from .models import Approval, StoredRun, StoredStep
from .state import RunStatus, TERMINAL_STATUSES, validate_status_transition

# Default database location, relative to the current working directory.
DEFAULT_DB_PATH = os.path.join(".autoprompt", "autoprompt.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    repo_path TEXT,
    created_at TEXT NOT NULL
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
"""


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


def init_db(db_path: Optional[str] = None) -> str:
    """Create the database file and tables if absent. Return the resolved path.

    The parent directory is created when it does not yet exist.
    """
    path = _resolve_db_path(db_path)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    conn = _connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return path


def create_run(
    db_path: str,
    root_prompt: str,
    provider: str,
    max_loops: int,
    require_approval: bool,
    project_id: Optional[int] = None,
) -> int:
    """Insert a new run in the CREATED state and return its id."""
    path = _resolve_db_path(db_path)
    conn = _connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO runs "
            "(project_id, root_prompt, provider, status, max_loops, require_approval, created_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                root_prompt,
                provider,
                RunStatus.CREATED.value,
                int(max_loops),
                1 if require_approval else 0,
                _utcnow_iso(),
                None,
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
