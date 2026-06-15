"""Tests for Claude Code app injection targets (autoprompt_runner.app_targets)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import app_targets, storage  # noqa: E402


class AppTargetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_list_get(self):
        target = app_targets.create_target(
            self.db, name="FactoryColony Claude", session_label="FactoryColony",
            target_mode="active_window_manual", submit_mode="paste_only",
        )
        self.assertEqual(target.status, "ACTIVE")
        self.assertEqual(target.app_name, "Claude Code")
        self.assertTrue(target.confirm_before_inject)
        self.assertEqual([t.name for t in app_targets.list_targets(self.db)], ["FactoryColony Claude"])
        self.assertEqual(app_targets.get_target(self.db, target.id).id, target.id)
        self.assertEqual(app_targets.get_target_by_name(self.db, "FactoryColony Claude").id, target.id)

    def test_duplicate_name_rejected(self):
        app_targets.create_target(self.db, name="dup")
        with self.assertRaises(app_targets.AppTargetError) as ctx:
            app_targets.create_target(self.db, name="dup")
        self.assertEqual(ctx.exception.kind, "duplicate")

    def test_empty_name_rejected(self):
        with self.assertRaises(app_targets.AppTargetError) as ctx:
            app_targets.create_target(self.db, name="   ")
        self.assertEqual(ctx.exception.kind, "invalid")

    def test_invalid_enums_rejected(self):
        with self.assertRaises(app_targets.AppTargetError):
            app_targets.create_target(self.db, name="a", target_mode="bogus")
        with self.assertRaises(app_targets.AppTargetError):
            app_targets.create_target(self.db, name="b", submit_mode="bogus")

    def test_enable_disable(self):
        t = app_targets.create_target(self.db, name="t")
        self.assertEqual(app_targets.disable_target(self.db, t.id).status, "DISABLED")
        self.assertEqual(app_targets.enable_target(self.db, t.id).status, "ACTIVE")

    def test_update(self):
        t = app_targets.create_target(self.db, name="t", submit_mode="paste_only")
        updated = app_targets.update_target(self.db, t.id, submit_mode="paste_and_enter", session_label="S")
        self.assertEqual(updated.submit_mode, "paste_and_enter")
        self.assertEqual(updated.session_label, "S")

    def test_update_invalid_enum_rejected(self):
        t = app_targets.create_target(self.db, name="t")
        with self.assertRaises(app_targets.AppTargetError):
            app_targets.update_target(self.db, t.id, submit_mode="bogus")

    def test_delete(self):
        t = app_targets.create_target(self.db, name="t")
        app_targets.delete_target(self.db, t.id)
        self.assertIsNone(app_targets.get_target(self.db, t.id))

    def test_require_missing_raises(self):
        with self.assertRaises(app_targets.AppTargetError) as ctx:
            app_targets.require_target(self.db, 9999)
        self.assertEqual(ctx.exception.kind, "not_found")

    def test_mark_used(self):
        t = app_targets.create_target(self.db, name="t")
        self.assertIsNone(t.last_used_at)
        storage.mark_app_target_used(self.db, t.id)
        self.assertIsNotNone(app_targets.get_target(self.db, t.id).last_used_at)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_fingerprint_generated_and_changes(self):
        a = app_targets.create_target(self.db, name="A", expected_window_title="FC")
        self.assertTrue(a.target_fingerprint)
        b = app_targets.create_target(self.db, name="B", expected_window_title="OTHER")
        self.assertNotEqual(a.target_fingerprint, b.target_fingerprint)

    def test_expected_fields_default_from_descriptive(self):
        t = app_targets.create_target(self.db, name="t", session_label="FactoryColony", app_name="Claude Code")
        self.assertEqual(t.expected_session_label, "FactoryColony")
        self.assertEqual(t.expected_app_name, "Claude Code")

    def test_invalid_verification_mode_rejected(self):
        with self.assertRaises(app_targets.AppTargetError):
            app_targets.create_target(self.db, name="t", verification_mode="bogus")

    def test_invalid_target_kind_rejected(self):
        with self.assertRaises(app_targets.AppTargetError):
            app_targets.create_target(self.db, name="t", target_kind="bogus")

    def test_require_target_confirmation(self):
        manual = app_targets.create_target(self.db, name="m", verification_mode="manual_confirm")
        self.assertFalse(app_targets.require_target_confirmation(manual, user_confirmed=False))
        self.assertTrue(app_targets.require_target_confirmation(manual, user_confirmed=True))

    def test_verify_manual_confirm_persists_status(self):
        t = app_targets.create_target(self.db, name="t", verification_mode="manual_confirm")
        result = app_targets.verify_app_target(self.db, t.id)
        self.assertEqual(result.status, "manual_required")
        self.assertEqual(app_targets.get_target(self.db, t.id).last_verification_status, "manual_required")

    def test_safety_summary_requires_confirmation(self):
        t = app_targets.create_target(self.db, name="t", verification_mode="manual_confirm", session_label="FC")
        summary = app_targets.build_injection_safety_summary(self.db, t)
        self.assertTrue(summary.requires_confirmation)
        self.assertFalse(summary.mismatch)
        self.assertEqual(summary.expected_session_label, "FC")
        self.assertTrue(summary.warnings)  # manual confirmation warning


if __name__ == "__main__":
    unittest.main()
