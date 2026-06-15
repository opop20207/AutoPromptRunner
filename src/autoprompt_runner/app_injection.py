"""Safe, clipboard-based prompt injection into the active desktop window.

This is the MVP injection backend for the Claude Code app: it copies a prompt to the system
clipboard and (when a keyboard-automation backend is available) sends the paste hotkey
(``Ctrl+V`` / ``Cmd+V`` on macOS) and an optional submit hotkey, into **the active window**. The
user is responsible for focusing the correct Claude Code input first -- this module never
detects, raises, focuses, or pastes into a window on its own, and it is only ever called on an
explicit user action (see :mod:`prompt_queue`).

Optional dependencies (kept out of the required install):

* ``pyperclip`` -- robust cross-platform clipboard. Without it, a best-effort standard-library
  clipboard (``clip`` / ``pbcopy`` / ``xclip`` / ``xsel`` and their read counterparts) is used.
* ``pyautogui`` -- the paste/submit hotkeys. Without it, injection runs in **clipboard-only**
  mode: the prompt is placed on the clipboard and the user pastes it manually (``Ctrl+V``).

Tests monkeypatch the small set of seam functions below, so they never touch a real desktop,
clipboard, or the Claude Code app. Standard library only at import time; the GUI backends are
imported lazily so importing this module never fails.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

from .app_targets import (
    SUBMIT_MODE_PASTE_AND_CTRL_ENTER,
    SUBMIT_MODE_PASTE_AND_ENTER,
    SUBMIT_MODE_PASTE_ONLY,
    STATUS_DISABLED,
)


class InjectionError(Exception):
    """Raised when an injection request is invalid or cannot be performed.

    ``kind`` is ``"empty"`` (empty prompt), ``"disabled"`` (target disabled), ``"not_found"``
    (no target), or ``"clipboard"`` (the clipboard could not be set). Callers map it to a CLI
    exit code or an HTTP status (400 / 409).
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class InjectionResult:
    """What an injection attempt actually did (no prompt text is included)."""

    clipboard_set: bool
    paste_sent: bool
    submit_sent: bool
    clipboard_restored: bool
    automation_available: bool
    submit_mode: str
    message: str


def _have_pyperclip() -> bool:
    try:
        import pyperclip  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - any import/runtime failure means "not available"
        return False


def _have_pyautogui() -> bool:
    try:
        import pyautogui  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _paste_modifier() -> str:
    """Return the platform paste modifier key (``command`` on macOS, else ``ctrl``)."""
    return "command" if sys.platform == "darwin" else "ctrl"


# -- clipboard ---------------------------------------------------------------


def _set_clipboard_text(text: str) -> bool:
    """Set the system clipboard. Prefers pyperclip; falls back to a platform CLI tool."""
    if _have_pyperclip():
        try:
            import pyperclip
            pyperclip.copy(text)
            return True
        except Exception:  # noqa: BLE001
            return False
    return _stdlib_clipboard_write(text)


def _read_clipboard_text() -> Optional[str]:
    """Read the system clipboard (best-effort). Returns ``None`` if unavailable."""
    if _have_pyperclip():
        try:
            import pyperclip
            return pyperclip.paste()
        except Exception:  # noqa: BLE001
            return None
    return _stdlib_clipboard_read()


def _stdlib_clipboard_write(text: str) -> bool:
    """Best-effort clipboard write via a platform CLI tool (never uses a shell)."""
    for argv in _clipboard_write_commands():
        try:
            completed = subprocess.run(argv, input=text, text=True, shell=False, capture_output=True)
            if completed.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def _stdlib_clipboard_read() -> Optional[str]:
    """Best-effort clipboard read via a platform CLI tool. Returns ``None`` on failure."""
    for argv in _clipboard_read_commands():
        try:
            completed = subprocess.run(argv, capture_output=True, text=True, shell=False)
            if completed.returncode == 0:
                return completed.stdout
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _clipboard_write_commands():
    if sys.platform == "win32":
        return [["clip"]]
    if sys.platform == "darwin":
        return [["pbcopy"]]
    return [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]


def _clipboard_read_commands():
    if sys.platform == "win32":
        return [["powershell", "-NoProfile", "-Command", "Get-Clipboard"]]
    if sys.platform == "darwin":
        return [["pbpaste"]]
    return [["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]]


def backup_clipboard() -> Optional[str]:
    """Return the current clipboard text so it can be restored later (best-effort)."""
    return _read_clipboard_text()


def restore_clipboard(previous_value: Optional[str]) -> bool:
    """Restore a previously backed-up clipboard value. Returns True if it was set."""
    if previous_value is None:
        return False
    return _set_clipboard_text(previous_value)


def copy_prompt_to_clipboard(prompt: str) -> bool:
    """Copy a prompt onto the system clipboard. Returns True on success."""
    return _set_clipboard_text(prompt)


# -- hotkeys -----------------------------------------------------------------


def send_paste_hotkey() -> bool:
    """Send the paste hotkey to the active window. Returns False if automation is unavailable."""
    if not _have_pyautogui():
        return False
    import pyautogui
    pyautogui.hotkey(_paste_modifier(), "v")
    return True


def send_submit_hotkey(submit_mode: str) -> bool:
    """Send the submit hotkey for ``submit_mode``. No-op for paste_only or without automation."""
    if submit_mode == SUBMIT_MODE_PASTE_ONLY:
        return False
    if not _have_pyautogui():
        return False
    import pyautogui
    if submit_mode == SUBMIT_MODE_PASTE_AND_ENTER:
        pyautogui.press("enter")
        return True
    if submit_mode == SUBMIT_MODE_PASTE_AND_CTRL_ENTER:
        pyautogui.hotkey("ctrl", "enter")
        return True
    return False


# -- orchestration -----------------------------------------------------------


def validate_injection_request(target, prompt: str) -> None:
    """Validate an injection request (target present + active, non-empty prompt). Raises on error."""
    if target is None:
        raise InjectionError("not_found", "no app target bound to this queue")
    if getattr(target, "status", None) == STATUS_DISABLED:
        raise InjectionError("disabled", f"app target '{getattr(target, 'name', '?')}' is disabled")
    if not (prompt or "").strip():
        raise InjectionError("empty", "cannot inject an empty prompt")


def inject_prompt_to_active_window(
    prompt: str,
    submit_mode: str = SUBMIT_MODE_PASTE_ONLY,
    restore_clipboard_after: bool = False,
    dry_run: bool = False,
) -> InjectionResult:
    """Copy ``prompt`` to the clipboard and (if automation is available) paste/submit it.

    Never injects an empty prompt. When ``dry_run`` is true, the clipboard is **not** modified and
    nothing is pasted -- it returns a result describing what would happen (used for the safety
    preview). When ``pyautogui`` is unavailable this runs in clipboard-only mode (the prompt is on
    the clipboard for the user to paste manually). The clipboard is only restored when a paste was
    actually sent (otherwise the prompt must stay on the clipboard for the manual paste). The
    prompt text is never logged.
    """
    if not (prompt or "").strip():
        raise InjectionError("empty", "cannot inject an empty prompt")

    if dry_run:
        return InjectionResult(
            clipboard_set=False, paste_sent=False, submit_sent=False, clipboard_restored=False,
            automation_available=_have_pyautogui(), submit_mode=submit_mode,
            message="dry run: clipboard and keyboard were not touched",
        )

    previous = backup_clipboard() if restore_clipboard_after else None
    if not copy_prompt_to_clipboard(prompt):
        raise InjectionError(
            "clipboard",
            "could not set the clipboard (install 'pyperclip', or a clipboard tool like xclip on Linux)",
        )

    paste_sent = send_paste_hotkey()
    submit_sent = send_submit_hotkey(submit_mode) if paste_sent else False
    restored = restore_clipboard(previous) if (restore_clipboard_after and paste_sent) else False
    automation = _have_pyautogui()

    if paste_sent:
        message = "prompt pasted into the active window"
        if submit_sent:
            message += " and submitted"
        else:
            message += " (submit it in Claude Code)"
    else:
        message = "prompt copied to the clipboard -- focus the Claude Code input and paste it (Ctrl+V/Cmd+V)"
    return InjectionResult(
        clipboard_set=True, paste_sent=paste_sent, submit_sent=submit_sent,
        clipboard_restored=restored, automation_available=automation, submit_mode=submit_mode,
        message=message,
    )
