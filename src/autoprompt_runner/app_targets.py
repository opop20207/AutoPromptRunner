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

from dataclasses import dataclass, field
from typing import List, Optional

from . import storage, window_detection
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

# target_kind values (how the target is identified). Only metadata in this step.
TARGET_KIND_ACTIVE_WINDOW = "active_window"
TARGET_KIND_WINDOW_TITLE = "window_title"
TARGET_KIND_MANUAL_SESSION = "manual_session"
TARGET_KIND_MANUAL_PANE = "manual_pane"
TARGET_KINDS = (
    TARGET_KIND_ACTIVE_WINDOW, TARGET_KIND_WINDOW_TITLE, TARGET_KIND_MANUAL_SESSION, TARGET_KIND_MANUAL_PANE,
)

# verification_mode values. manual_confirm is fully supported; the *_hint modes are best-effort.
VERIFICATION_MANUAL_CONFIRM = "manual_confirm"
VERIFICATION_WINDOW_TITLE_HINT = "window_title_hint"
VERIFICATION_APP_NAME_HINT = "app_name_hint"
VERIFICATION_NONE = "none"
VERIFICATION_MODES = (
    VERIFICATION_MANUAL_CONFIRM, VERIFICATION_WINDOW_TITLE_HINT, VERIFICATION_APP_NAME_HINT, VERIFICATION_NONE,
)

# Verification result statuses (re-exported from window_detection).
VERIFY_VERIFIED = window_detection.STATUS_VERIFIED
VERIFY_MISMATCH = window_detection.STATUS_MISMATCH
VERIFY_UNAVAILABLE = window_detection.STATUS_UNAVAILABLE
VERIFY_MANUAL_REQUIRED = window_detection.STATUS_MANUAL_REQUIRED

DEFAULT_APP_NAME = "Claude Code"


@dataclass
class InjectionSafetySummary:
    """A read-only safety snapshot used to confirm an injection target (no side effects)."""

    target_id: int
    target_name: str
    target_status: str
    target_kind: str
    verification_mode: str
    submit_mode: str
    expected_app_name: Optional[str]
    expected_window_title: Optional[str]
    expected_session_label: Optional[str]
    expected_project_path: Optional[str]
    expected_pane_label: Optional[str]
    expected_pane_index: Optional[int]
    active_window: Optional[window_detection.WindowInfo]
    active_window_summary: str
    verification_status: str
    verification_message: str
    matched: Optional[bool]
    mismatch: bool
    requires_confirmation: bool
    warnings: List[str] = field(default_factory=list)


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
    target_kind: str = TARGET_KIND_ACTIVE_WINDOW,
    verification_mode: str = VERIFICATION_MANUAL_CONFIRM,
    expected_window_title: Optional[str] = None,
    expected_app_name: Optional[str] = None,
    expected_session_label: Optional[str] = None,
    expected_project_path: Optional[str] = None,
    expected_pane_label: Optional[str] = None,
    expected_pane_index: Optional[int] = None,
) -> AppTarget:
    """Create an app target after validating its name and enums. Raises on a duplicate name.

    Unset ``expected_*`` verification fields default from the descriptive fields, and a stable
    ``target_fingerprint`` is computed so a later change to the binding can be detected.
    """
    db_path = storage.init_db(db_path)
    clean_name = (name or "").strip()
    if not clean_name:
        raise AppTargetError("invalid", "app target name must not be empty")
    if not (app_name or "").strip():
        raise AppTargetError("invalid", "app_name must not be empty")
    _validate_enums(target_mode, submit_mode)
    _validate_verification(target_kind, verification_mode)
    if storage.get_app_target_by_name(db_path, clean_name) is not None:
        raise AppTargetError("duplicate", f"an app target named '{clean_name}' already exists")

    # Default the expected_* verification fields from the descriptive fields when unset.
    expected_app_name = expected_app_name if expected_app_name is not None else app_name
    expected_window_title = expected_window_title if expected_window_title is not None else window_title_hint
    expected_session_label = expected_session_label if expected_session_label is not None else session_label
    expected_project_path = expected_project_path if expected_project_path is not None else project_path
    expected_pane_label = expected_pane_label if expected_pane_label is not None else pane_label
    expected_pane_index = expected_pane_index if expected_pane_index is not None else pane_index

    fingerprint = window_detection.build_target_fingerprint(_FingerprintInput(
        target_kind=target_kind, expected_app_name=expected_app_name, expected_window_title=expected_window_title,
        expected_session_label=expected_session_label, expected_project_path=expected_project_path,
        expected_pane_label=expected_pane_label, expected_pane_index=expected_pane_index,
    ))
    target_id = storage.create_app_target(
        db_path, name=clean_name, app_name=app_name, target_mode=target_mode, submit_mode=submit_mode,
        window_title_hint=window_title_hint, session_label=session_label, project_path=project_path,
        worktree_path=worktree_path, pane_label=pane_label, pane_index=pane_index,
        confirm_before_inject=confirm_before_inject, target_kind=target_kind, verification_mode=verification_mode,
        target_fingerprint=fingerprint, expected_window_title=expected_window_title, expected_app_name=expected_app_name,
        expected_session_label=expected_session_label, expected_project_path=expected_project_path,
        expected_pane_label=expected_pane_label, expected_pane_index=expected_pane_index,
    )
    return storage.get_app_target(db_path, target_id)


@dataclass
class _FingerprintInput:
    """Lightweight duck-typed object passed to window_detection.build_target_fingerprint."""

    target_kind: str
    expected_app_name: Optional[str]
    expected_window_title: Optional[str]
    expected_session_label: Optional[str]
    expected_project_path: Optional[str]
    expected_pane_label: Optional[str]
    expected_pane_index: Optional[int]


def _validate_verification(target_kind: str, verification_mode: str) -> None:
    if target_kind not in TARGET_KINDS:
        raise AppTargetError("invalid", f"target_kind must be one of: {', '.join(TARGET_KINDS)}")
    if verification_mode not in VERIFICATION_MODES:
        raise AppTargetError("invalid", f"verification_mode must be one of: {', '.join(VERIFICATION_MODES)}")


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
    """Update an app target (validating any enum fields) and return the refreshed record.

    Recomputes ``target_fingerprint`` when any identifying field changes.
    """
    require_target(db_path, target_id)
    if "target_mode" in fields and fields["target_mode"] not in TARGET_MODES:
        raise AppTargetError("invalid", f"target_mode must be one of: {', '.join(TARGET_MODES)}")
    if "submit_mode" in fields and fields["submit_mode"] not in SUBMIT_MODES:
        raise AppTargetError("invalid", f"submit_mode must be one of: {', '.join(SUBMIT_MODES)}")
    if "target_kind" in fields and fields["target_kind"] not in TARGET_KINDS:
        raise AppTargetError("invalid", f"target_kind must be one of: {', '.join(TARGET_KINDS)}")
    if "verification_mode" in fields and fields["verification_mode"] not in VERIFICATION_MODES:
        raise AppTargetError("invalid", f"verification_mode must be one of: {', '.join(VERIFICATION_MODES)}")
    storage.update_app_target(db_path, target_id, **fields)
    refreshed = storage.get_app_target(db_path, target_id)
    new_fingerprint = window_detection.build_target_fingerprint(refreshed)
    if new_fingerprint != refreshed.target_fingerprint:
        storage.update_app_target(db_path, target_id, target_fingerprint=new_fingerprint)
        refreshed = storage.get_app_target(db_path, target_id)
    return refreshed


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


# -- verification ------------------------------------------------------------


def mark_target_verified(db_path: str, target_id: int, status: str, message: Optional[str] = None) -> AppTarget:
    """Persist a verification result on the target and return the refreshed record."""
    require_target(db_path, target_id)
    storage.mark_app_target_verified(db_path, target_id, status, message)
    return storage.get_app_target(db_path, target_id)


def verify_app_target(db_path: str, target_id: int) -> "window_detection.VerificationResult":
    """Run a best-effort verification of the active window against the target and persist it."""
    target = require_target(db_path, target_id)
    result = window_detection.verify_active_window_against_target(target)
    storage.mark_app_target_verified(db_path, target_id, result.status, result.message)
    return result


def require_target_confirmation(target: AppTarget, user_confirmed: bool) -> bool:
    """Return True if confirmation requirements are satisfied for injecting into ``target``.

    Confirmation is required when the target uses ``manual_confirm`` verification or has
    ``confirm_before_inject`` set (the safe default). When required, ``user_confirmed`` must be
    True; otherwise injection may proceed without an explicit per-injection confirmation.
    """
    required = target.verification_mode == VERIFICATION_MANUAL_CONFIRM or target.confirm_before_inject
    return bool(user_confirmed) if required else True


def build_injection_safety_summary(db_path: str, target: AppTarget) -> InjectionSafetySummary:
    """Assemble a read-only injection safety summary (verification + warnings). No side effects."""
    result = window_detection.verify_active_window_against_target(target)
    mismatch = result.status == VERIFY_MISMATCH
    requires_confirmation = (
        target.verification_mode == VERIFICATION_MANUAL_CONFIRM or target.confirm_before_inject
    )
    warnings: List[str] = []
    if target.status == STATUS_DISABLED:
        warnings.append("target is DISABLED; injection will be refused")
    if mismatch:
        warnings.append("active window does NOT match this target — injecting may hit the wrong session")
    if result.status == VERIFY_UNAVAILABLE:
        warnings.append("active window could not be detected; rely on manual confirmation")
    if result.status == VERIFY_MANUAL_REQUIRED:
        warnings.append("manual confirmation required: focus the correct Claude Code input first")
    return InjectionSafetySummary(
        target_id=target.id, target_name=target.name, target_status=target.status,
        target_kind=target.target_kind, verification_mode=target.verification_mode, submit_mode=target.submit_mode,
        expected_app_name=target.expected_app_name, expected_window_title=target.expected_window_title,
        expected_session_label=target.expected_session_label, expected_project_path=target.expected_project_path,
        expected_pane_label=target.expected_pane_label, expected_pane_index=target.expected_pane_index,
        active_window=result.window, active_window_summary=window_detection.safe_window_summary(result.window),
        verification_status=result.status, verification_message=result.message, matched=result.matched,
        mismatch=mismatch, requires_confirmation=requires_confirmation, warnings=warnings,
    )
