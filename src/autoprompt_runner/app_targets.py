"""Claude Code **app** injection targets.

An app target names a *specific* Claude Code desktop-app session/pane (not just "the Claude
Code app"), so a queued prompt is injected into the place the user intends. This module owns
the target enums, validation, and thin create/update helpers over :mod:`storage`. It performs
no injection itself (see :mod:`app_injection`) and reads no secrets.

For this step only ``target_mode = active_window_manual`` is implemented: the user manually
focuses the correct Claude Code input and AutoPromptRunner injects into the active window.
``window_title_hint`` and ``future_accessibility`` are accepted as forward-compatible values
but behave like the manual mode (no automatic window/pane detection yet).
"""

from __future__ import annotations

from typing import List, Optional

from . import storage
from .models import AppTarget

# target_mode values.
TARGET_MODE_ACTIVE_WINDOW_MANUAL = "active_window_manual"
TARGET_MODE_WINDOW_TITLE_HINT = "window_title_hint"
TARGET_MODE_FUTURE_ACCESSIBILITY = "future_accessibility"
TARGET_MODES = (
    TARGET_MODE_ACTIVE_WINDOW_MANUAL,
    TARGET_MODE_WINDOW_TITLE_HINT,
    TARGET_MODE_FUTURE_ACCESSIBILITY,
)
# Only this mode actually performs injection in this step.
IMPLEMENTED_TARGET_MODES = (TARGET_MODE_ACTIVE_WINDOW_MANUAL,)

# submit_mode values.
SUBMIT_MODE_PASTE_ONLY = "paste_only"
SUBMIT_MODE_PASTE_AND_ENTER = "paste_and_enter"
SUBMIT_MODE_PASTE_AND_CTRL_ENTER = "paste_and_ctrl_enter"
SUBMIT_MODES = (SUBMIT_MODE_PASTE_ONLY, SUBMIT_MODE_PASTE_AND_ENTER, SUBMIT_MODE_PASTE_AND_CTRL_ENTER)

# status values.
STATUS_ACTIVE = storage.APP_TARGET_ACTIVE
STATUS_DISABLED = storage.APP_TARGET_DISABLED

DEFAULT_APP_NAME = "Claude Code"


class AppTargetError(Exception):
    """Raised for invalid app-target input or a missing/duplicate target.

    ``kind`` is ``"invalid"``, ``"not_found"``, or ``"duplicate"``; callers map it to a CLI exit
    code or an HTTP status (400 / 404 / 409).
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _validate_enums(target_mode: str, submit_mode: str) -> None:
    if target_mode not in TARGET_MODES:
        raise AppTargetError("invalid", f"target_mode must be one of: {', '.join(TARGET_MODES)}")
    if submit_mode not in SUBMIT_MODES:
        raise AppTargetError("invalid", f"submit_mode must be one of: {', '.join(SUBMIT_MODES)}")


def create_target(
    db_path: str,
    *,
    name: str,
    app_name: str = DEFAULT_APP_NAME,
    target_mode: str = TARGET_MODE_ACTIVE_WINDOW_MANUAL,
    submit_mode: str = SUBMIT_MODE_PASTE_ONLY,
    window_title_hint: Optional[str] = None,
    session_label: Optional[str] = None,
    project_path: Optional[str] = None,
    worktree_path: Optional[str] = None,
    pane_label: Optional[str] = None,
    pane_index: Optional[int] = None,
    confirm_before_inject: bool = True,
) -> AppTarget:
    """Create an app target after validating its name and enums. Raises on a duplicate name."""
    db_path = storage.init_db(db_path)
    clean_name = (name or "").strip()
    if not clean_name:
        raise AppTargetError("invalid", "app target name must not be empty")
    if not (app_name or "").strip():
        raise AppTargetError("invalid", "app_name must not be empty")
    _validate_enums(target_mode, submit_mode)
    if storage.get_app_target_by_name(db_path, clean_name) is not None:
        raise AppTargetError("duplicate", f"an app target named '{clean_name}' already exists")
    target_id = storage.create_app_target(
        db_path, name=clean_name, app_name=app_name, target_mode=target_mode, submit_mode=submit_mode,
        window_title_hint=window_title_hint, session_label=session_label, project_path=project_path,
        worktree_path=worktree_path, pane_label=pane_label, pane_index=pane_index,
        confirm_before_inject=confirm_before_inject,
    )
    return storage.get_app_target(db_path, target_id)


def list_targets(db_path: str) -> List[AppTarget]:
    """Return all app targets."""
    return storage.list_app_targets(db_path)


def get_target(db_path: str, target_id: int) -> Optional[AppTarget]:
    """Return an app target by id, or ``None``."""
    return storage.get_app_target(db_path, target_id)


def get_target_by_name(db_path: str, name: str) -> Optional[AppTarget]:
    """Return an app target by name, or ``None``."""
    return storage.get_app_target_by_name(db_path, name)


def require_target(db_path: str, target_id: int) -> AppTarget:
    """Return an app target by id or raise ``AppTargetError('not_found')``."""
    target = storage.get_app_target(db_path, target_id)
    if target is None:
        raise AppTargetError("not_found", f"app target {target_id} not found")
    return target


def update_target(db_path: str, target_id: int, **fields) -> AppTarget:
    """Update an app target (validating any enum fields) and return the refreshed record."""
    require_target(db_path, target_id)
    if "target_mode" in fields and fields["target_mode"] not in TARGET_MODES:
        raise AppTargetError("invalid", f"target_mode must be one of: {', '.join(TARGET_MODES)}")
    if "submit_mode" in fields and fields["submit_mode"] not in SUBMIT_MODES:
        raise AppTargetError("invalid", f"submit_mode must be one of: {', '.join(SUBMIT_MODES)}")
    storage.update_app_target(db_path, target_id, **fields)
    return storage.get_app_target(db_path, target_id)


def delete_target(db_path: str, target_id: int) -> None:
    """Delete an app target."""
    require_target(db_path, target_id)
    storage.delete_app_target(db_path, target_id)


def enable_target(db_path: str, target_id: int) -> AppTarget:
    """Set an app target ACTIVE."""
    require_target(db_path, target_id)
    storage.set_app_target_status(db_path, target_id, STATUS_ACTIVE)
    return storage.get_app_target(db_path, target_id)


def disable_target(db_path: str, target_id: int) -> AppTarget:
    """Set an app target DISABLED (injection is then refused)."""
    require_target(db_path, target_id)
    storage.set_app_target_status(db_path, target_id, STATUS_DISABLED)
    return storage.get_app_target(db_path, target_id)


def target_summary(target: AppTarget) -> str:
    """Return a compact one-line summary of a target (for CLI / API confirmation output)."""
    bits = [f"#{target.id} {target.name}", f"app={target.app_name}"]
    if target.session_label:
        bits.append(f"session={target.session_label}")
    if target.pane_label:
        bits.append(f"pane={target.pane_label}")
    bits.append(f"mode={target.target_mode}")
    bits.append(f"submit={target.submit_mode}")
    bits.append(f"status={target.status}")
    return " | ".join(bits)
