"""Tests for clipboard-based prompt injection (autoprompt_runner.app_injection).

The desktop seam functions are monkeypatched, so these tests never touch a real clipboard,
keyboard, or the Claude Code app. Standard library only.
"""

from __future__ import annotations

import os
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import app_injection  # noqa: E402
from autoprompt_runner.app_targets import (  # noqa: E402
    SUBMIT_MODE_PASTE_AND_ENTER,
    SUBMIT_MODE_PASTE_ONLY,
)


class _Target:
    def __init__(self, status="ACTIVE", name="t"):
        self.status = status
        self.name = name


class ValidateTests(unittest.TestCase):
    def test_missing_target(self):
        with self.assertRaises(app_injection.InjectionError) as ctx:
            app_injection.validate_injection_request(None, "hi")
        self.assertEqual(ctx.exception.kind, "not_found")

    def test_disabled_target(self):
        with self.assertRaises(app_injection.InjectionError) as ctx:
            app_injection.validate_injection_request(_Target(status="DISABLED"), "hi")
        self.assertEqual(ctx.exception.kind, "disabled")

    def test_empty_prompt(self):
        with self.assertRaises(app_injection.InjectionError) as ctx:
            app_injection.validate_injection_request(_Target(), "   ")
        self.assertEqual(ctx.exception.kind, "empty")

    def test_valid(self):
        app_injection.validate_injection_request(_Target(), "do it")  # no raise


class InjectTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._orig = {
            name: getattr(app_injection, name)
            for name in ("copy_prompt_to_clipboard", "send_paste_hotkey", "send_submit_hotkey",
                         "backup_clipboard", "restore_clipboard")
        }
        # Default fakes: clipboard + paste + submit all succeed.
        app_injection.copy_prompt_to_clipboard = lambda prompt: (self.calls.append(("copy", prompt)) or True)
        app_injection.send_paste_hotkey = lambda: (self.calls.append(("paste",)) or True)
        app_injection.send_submit_hotkey = lambda mode: (self.calls.append(("submit", mode)) or (mode != SUBMIT_MODE_PASTE_ONLY))
        app_injection.backup_clipboard = lambda: "PREVIOUS"
        app_injection.restore_clipboard = lambda prev: (self.calls.append(("restore", prev)) or True)

    def tearDown(self):
        for name, fn in self._orig.items():
            setattr(app_injection, name, fn)

    def test_empty_prompt_raises(self):
        with self.assertRaises(app_injection.InjectionError) as ctx:
            app_injection.inject_prompt_to_active_window("  ")
        self.assertEqual(ctx.exception.kind, "empty")

    def test_paste_only(self):
        result = app_injection.inject_prompt_to_active_window("do it", submit_mode=SUBMIT_MODE_PASTE_ONLY)
        self.assertTrue(result.clipboard_set)
        self.assertTrue(result.paste_sent)
        self.assertFalse(result.submit_sent)  # paste_only never submits
        self.assertIn(("copy", "do it"), self.calls)

    def test_paste_and_enter_submits(self):
        result = app_injection.inject_prompt_to_active_window("do it", submit_mode=SUBMIT_MODE_PASTE_AND_ENTER)
        self.assertTrue(result.submit_sent)
        self.assertIn(("submit", SUBMIT_MODE_PASTE_AND_ENTER), self.calls)

    def test_clipboard_failure_raises(self):
        app_injection.copy_prompt_to_clipboard = lambda prompt: False
        with self.assertRaises(app_injection.InjectionError) as ctx:
            app_injection.inject_prompt_to_active_window("do it")
        self.assertEqual(ctx.exception.kind, "clipboard")

    def test_restore_after_paste(self):
        result = app_injection.inject_prompt_to_active_window("do it", restore_clipboard_after=True)
        self.assertTrue(result.clipboard_restored)
        self.assertIn(("restore", "PREVIOUS"), self.calls)

    def test_no_restore_when_paste_unavailable(self):
        # Clipboard-only mode (no automation): the prompt must stay on the clipboard, not be restored.
        app_injection.send_paste_hotkey = lambda: False
        result = app_injection.inject_prompt_to_active_window("do it", restore_clipboard_after=True)
        self.assertFalse(result.paste_sent)
        self.assertFalse(result.clipboard_restored)
        self.assertNotIn(("restore", "PREVIOUS"), self.calls)

    def test_dry_run_touches_nothing(self):
        result = app_injection.inject_prompt_to_active_window("do it", dry_run=True)
        self.assertFalse(result.clipboard_set)
        self.assertFalse(result.paste_sent)
        self.assertFalse(result.submit_sent)
        self.assertEqual(self.calls, [])  # neither clipboard nor hotkeys were used
        self.assertIn("dry run", result.message)


if __name__ == "__main__":
    unittest.main()
