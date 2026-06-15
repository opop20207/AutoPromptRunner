"""Tests for best-effort window detection + verification (autoprompt_runner.window_detection).

The active-window reader is mocked, so these never depend on real OS windows or accessibility
permissions. Standard library only.
"""

from __future__ import annotations

import os
import sys
import types
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import window_detection as wd  # noqa: E402


def _target(**kwargs):
    base = dict(
        target_kind="active_window", verification_mode="manual_confirm", app_name="Claude Code",
        window_title_hint=None, session_label=None, project_path=None, pane_label=None, pane_index=None,
        expected_app_name=None, expected_window_title=None, expected_session_label=None,
        expected_project_path=None, expected_pane_label=None, expected_pane_index=None,
    )
    base.update(kwargs)
    return types.SimpleNamespace(**base)


class FingerprintTests(unittest.TestCase):
    def test_stable_and_short(self):
        t = _target(expected_app_name="Claude", expected_window_title="FC")
        fp = wd.build_target_fingerprint(t)
        self.assertEqual(fp, wd.build_target_fingerprint(t))  # stable
        self.assertEqual(len(fp), 16)

    def test_changes_with_metadata(self):
        a = wd.build_target_fingerprint(_target(expected_window_title="A"))
        b = wd.build_target_fingerprint(_target(expected_window_title="B"))
        self.assertNotEqual(a, b)


class GetActiveWindowTests(unittest.TestCase):
    def test_returns_window_info_without_raising(self):
        info = wd.get_active_window_info()
        self.assertIsInstance(info, wd.WindowInfo)
        self.assertIsInstance(info.available, bool)
        if not info.available:
            self.assertTrue(info.reason)

    def test_list_candidate_windows_returns_list(self):
        self.assertIsInstance(wd.list_candidate_windows(), list)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self._orig = wd.get_active_window_info

    def tearDown(self):
        wd.get_active_window_info = self._orig

    def _mock_window(self, **kwargs):
        defaults = dict(title="t", app_name="a", process_name="a", pid=1, platform="win32", available=True)
        defaults.update(kwargs)
        wd.get_active_window_info = lambda: wd.WindowInfo(**defaults)

    def test_manual_confirm_does_not_read_window(self):
        called = []
        wd.get_active_window_info = lambda: called.append(1)
        result = wd.verify_active_window_against_target(_target(verification_mode="manual_confirm"))
        self.assertEqual(result.status, wd.STATUS_MANUAL_REQUIRED)
        self.assertEqual(called, [])  # no OS read for manual_confirm

    def test_window_title_hint_match(self):
        self._mock_window(title="FactoryColony — Claude")
        result = wd.verify_active_window_against_target(
            _target(verification_mode="window_title_hint", expected_window_title="FactoryColony")
        )
        self.assertEqual(result.status, wd.STATUS_VERIFIED)
        self.assertTrue(result.matched)

    def test_window_title_hint_mismatch(self):
        self._mock_window(title="Other Project")
        result = wd.verify_active_window_against_target(
            _target(verification_mode="window_title_hint", expected_window_title="FactoryColony")
        )
        self.assertEqual(result.status, wd.STATUS_MISMATCH)
        self.assertFalse(result.matched)

    def test_app_name_hint_match(self):
        self._mock_window(app_name="Claude.exe", process_name="Claude.exe")
        result = wd.verify_active_window_against_target(
            _target(verification_mode="app_name_hint", expected_app_name="Claude")
        )
        self.assertEqual(result.status, wd.STATUS_VERIFIED)

    def test_unavailable_window(self):
        wd.get_active_window_info = lambda: wd.WindowInfo(
            title=None, app_name=None, process_name=None, pid=None, platform="linux", available=False, reason="nope",
        )
        result = wd.verify_active_window_against_target(
            _target(verification_mode="window_title_hint", expected_window_title="X")
        )
        self.assertEqual(result.status, wd.STATUS_UNAVAILABLE)


class SafeSummaryTests(unittest.TestCase):
    def test_unavailable(self):
        info = wd.WindowInfo(None, None, None, None, "linux", False, reason="not supported")
        self.assertIn("unavailable", wd.safe_window_summary(info))
        self.assertIn("unavailable", wd.safe_window_summary(None))

    def test_truncates_long_title(self):
        info = wd.WindowInfo("x" * 200, "app", "app", 5, "win32", True)
        summary = wd.safe_window_summary(info)
        self.assertIn("…", summary)
        self.assertIn("pid=5", summary)


if __name__ == "__main__":
    unittest.main()
