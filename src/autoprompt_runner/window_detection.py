"""Best-effort, cross-platform active-window detection for target verification.

This is a small, safe abstraction used to *reduce the risk* of injecting a prompt into the
wrong Claude Code session/pane. It can read the **active window title** (and, on Windows, the
owning process name) and compare it against an app target's expectations. It is deliberately
limited and best-effort:

* No OCR, no screenshots, no browser automation, no automatic pane detection.
* When the active window cannot be read (unsupported platform, missing OS support, any error),
  it returns ``available=False`` with a clean reason rather than raising -- and injection still
  works through the manual-confirmation path.
* It never logs window contents; :func:`safe_window_summary` truncates the title.

Standard library only (``ctypes`` on Windows, guarded). Tests mock these functions, so they
never depend on real OS windows or accessibility permissions.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from typing import List, Optional

# Verification result statuses.
STATUS_VERIFIED = "verified"
STATUS_MISMATCH = "mismatch"
STATUS_UNAVAILABLE = "unavailable"
STATUS_MANUAL_REQUIRED = "manual_required"

# Title length kept in a safe summary (avoid echoing long/sensitive window contents).
_SUMMARY_TITLE_CHARS = 80


@dataclass
class WindowInfo:
    """Information about a desktop window (best-effort)."""

    title: Optional[str]
    app_name: Optional[str]
    process_name: Optional[str]
    pid: Optional[int]
    platform: str
    available: bool
    reason: Optional[str] = None


@dataclass
class VerificationResult:
    """The outcome of comparing the active window against a target."""

    status: str
    message: str
    window: Optional[WindowInfo]
    matched: Optional[bool]


# -- active window -----------------------------------------------------------


def _unavailable(reason: str) -> WindowInfo:
    return WindowInfo(
        title=None, app_name=None, process_name=None, pid=None, platform=sys.platform,
        available=False, reason=reason,
    )


def get_active_window_info() -> WindowInfo:
    """Return info about the active (foreground) window, best-effort.

    On Windows the title (and owning process name) is read via ``ctypes``. On other platforms
    -- or on any failure -- returns ``available=False`` with a reason; never raises.
    """
    if sys.platform == "win32":
        return _windows_active_window()
    return _unavailable(
        f"active window detection is not supported on '{sys.platform}' (manual confirmation is used)"
    )


def _windows_active_window() -> WindowInfo:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return _unavailable("no foreground window")
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value or ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = _windows_process_name(int(pid.value)) if pid.value else None
        return WindowInfo(
            title=title, app_name=process_name, process_name=process_name, pid=int(pid.value) or None,
            platform="win32", available=True,
        )
    except Exception as exc:  # noqa: BLE001 - detection is best-effort and must never raise
        return _unavailable(f"active window detection failed: {exc}")


def _windows_process_name(pid: int) -> Optional[str]:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(260)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return os.path.basename(buffer.value)
            return None
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


def list_candidate_windows() -> List[WindowInfo]:
    """Return visible, titled top-level windows (best-effort). Empty when unsupported."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        results: List[WindowInfo] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = (buffer.value or "").strip()
            if title:
                results.append(WindowInfo(
                    title=title, app_name=None, process_name=None, pid=None, platform="win32", available=True,
                ))
            return True

        user32.EnumWindows(_callback, 0)
        return results
    except Exception:  # noqa: BLE001
        return []


# -- verification ------------------------------------------------------------


def build_target_fingerprint(target) -> str:
    """Return a stable short fingerprint of a target's identifying metadata.

    Hashes the target's kind + expected app/window/session/project/pane fields so a change to
    the binding can be detected. ``target`` is any object exposing these attributes.
    """
    parts = [
        str(getattr(target, "target_kind", "") or ""),
        str(getattr(target, "expected_app_name", "") or getattr(target, "app_name", "") or ""),
        str(getattr(target, "expected_window_title", "") or getattr(target, "window_title_hint", "") or ""),
        str(getattr(target, "expected_session_label", "") or getattr(target, "session_label", "") or ""),
        str(getattr(target, "expected_project_path", "") or getattr(target, "project_path", "") or ""),
        str(getattr(target, "expected_pane_label", "") or getattr(target, "pane_label", "") or ""),
        str(getattr(target, "expected_pane_index", "") if getattr(target, "expected_pane_index", None) is not None else ""),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def verify_active_window_against_target(target) -> VerificationResult:
    """Compare the active window with ``target`` per its ``verification_mode`` (best-effort).

    ``manual_confirm`` / ``none`` always require manual confirmation (no automated check).
    ``window_title_hint`` / ``app_name_hint`` compare the active window when it can be read;
    if it cannot, the result is ``unavailable`` and the caller falls back to manual confirmation.
    """
    mode = (getattr(target, "verification_mode", None) or "manual_confirm")
    if mode in ("manual_confirm", "none"):
        msg = "manual confirmation required" if mode == "manual_confirm" else "verification disabled; manual confirmation required"
        return VerificationResult(STATUS_MANUAL_REQUIRED, msg, None, None)

    info = get_active_window_info()
    if not info.available:
        return VerificationResult(STATUS_UNAVAILABLE, info.reason or "active window unavailable", info, None)

    if mode == "window_title_hint":
        expected = (getattr(target, "expected_window_title", None) or getattr(target, "window_title_hint", None) or "").strip()
        if not expected:
            return VerificationResult(STATUS_MANUAL_REQUIRED, "no expected window title set; confirm manually", info, None)
        matched = expected.lower() in (info.title or "").lower()
        verb = "matches" if matched else "does not match"
        return VerificationResult(
            STATUS_VERIFIED if matched else STATUS_MISMATCH, f"active window title {verb} the expected hint", info, matched
        )

    if mode == "app_name_hint":
        expected = (getattr(target, "expected_app_name", None) or getattr(target, "app_name", None) or "").strip()
        if not expected:
            return VerificationResult(STATUS_MANUAL_REQUIRED, "no expected app name set; confirm manually", info, None)
        haystack = " ".join(p for p in (info.app_name, info.process_name) if p).lower()
        if not haystack:
            return VerificationResult(STATUS_UNAVAILABLE, "active window app/process name unavailable", info, None)
        matched = expected.lower() in haystack
        verb = "matches" if matched else "does not match"
        return VerificationResult(
            STATUS_VERIFIED if matched else STATUS_MISMATCH, f"active window app name {verb} the expected app", info, matched
        )

    return VerificationResult(STATUS_MANUAL_REQUIRED, "manual confirmation required", info, None)


def safe_window_summary(window_info: Optional[WindowInfo]) -> str:
    """Return a compact, non-sensitive one-line summary of a window (or 'unavailable')."""
    if window_info is None or not window_info.available:
        reason = window_info.reason if window_info is not None else "no window info"
        return f"unavailable ({reason})"
    title = (window_info.title or "").strip()
    if len(title) > _SUMMARY_TITLE_CHARS:
        title = title[:_SUMMARY_TITLE_CHARS] + "…"
    extra = []
    if window_info.app_name:
        extra.append(f"app={window_info.app_name}")
    if window_info.pid:
        extra.append(f"pid={window_info.pid}")
    suffix = f" ({', '.join(extra)})" if extra else ""
    return f"{title or '(no title)'}{suffix}"
