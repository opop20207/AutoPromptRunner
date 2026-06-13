"""Tests for prompt template storage, rendering, and seeding (standard library only).

Runnable via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import storage, templates  # noqa: E402


class _DbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()


class TemplateCrudTests(_DbTestCase):
    def test_create_list_get_delete(self):
        tid = templates.create_template(
            self.db, name="Custom", body="Do {{goal}}", description="desc", tags=["a", "b"]
        )
        self.assertIsInstance(tid, int)
        self.assertIn("Custom", [t.name for t in templates.list_templates(self.db)])
        by_id = templates.get_template_by_id(self.db, tid)
        by_name = templates.get_template_by_name(self.db, "Custom")
        self.assertIsNotNone(by_id)
        self.assertEqual(by_name.id, tid)
        self.assertEqual(by_name.tags, ["a", "b"])
        self.assertEqual(by_name.description, "desc")
        templates.delete_template(self.db, tid)
        self.assertIsNone(templates.get_template_by_name(self.db, "Custom"))

    def test_update_template(self):
        tid = templates.create_template(self.db, name="U", body="old")
        templates.update_template(self.db, tid, body="new", tags=["x"])
        updated = templates.get_template_by_id(self.db, tid)
        self.assertEqual(updated.body, "new")
        self.assertEqual(updated.tags, ["x"])


class SeedTests(_DbTestCase):
    def test_seed_inserts_builtins(self):
        result = templates.seed_templates(self.db)
        self.assertEqual(result["seeded"], len(templates.DEFAULT_TEMPLATES))
        self.assertEqual(result["skipped"], 0)
        names = [t.name for t in templates.list_templates(self.db)]
        self.assertIn("Fix failing tests", names)
        self.assertIn("Continue next task", names)

    def test_seed_is_idempotent_and_skips_existing(self):
        templates.seed_templates(self.db)
        result = templates.seed_templates(self.db)
        self.assertEqual(result["seeded"], 0)
        self.assertEqual(result["skipped"], len(templates.DEFAULT_TEMPLATES))

    def test_seed_does_not_overwrite_user_modified_template(self):
        templates.create_template(self.db, name="Fix failing tests", body="MY CUSTOM BODY")
        result = templates.seed_templates(self.db)
        self.assertGreaterEqual(result["skipped"], 1)
        kept = templates.get_template_by_name(self.db, "Fix failing tests")
        self.assertEqual(kept.body, "MY CUSTOM BODY")  # untouched by seed

    def test_seed_force_overwrites_existing(self):
        templates.create_template(self.db, name="Fix failing tests", body="MY CUSTOM BODY")
        templates.seed_templates(self.db, overwrite=True)
        kept = templates.get_template_by_name(self.db, "Fix failing tests")
        self.assertNotEqual(kept.body, "MY CUSTOM BODY")  # refreshed to the built-in body


class RenderTests(unittest.TestCase):
    def test_render_known_placeholders(self):
        body = "Work on {{project_name}} in {{workspace}}. Goal: {{goal}}."
        out = templates.render_template(
            body, templates.build_render_values(project_name="P", workspace="/ws", goal="do it")
        )
        self.assertEqual(out, "Work on P in /ws. Goal: do it.")

    def test_unknown_placeholders_remain_unchanged(self):
        out = templates.render_template("Known {{goal}} and {{not_a_placeholder}}.", {"goal": "G"})
        self.assertEqual(out, "Known G and {{not_a_placeholder}}.")

    def test_missing_values_render_empty(self):
        self.assertEqual(templates.render_template("[{{goal}}][{{last_error}}]", {}), "[][]")

    def test_render_is_literal_no_evaluation(self):
        # Expression-looking unknown placeholders are left verbatim; nothing is evaluated.
        out = templates.render_template("{{goal}} {{1+1}} {{__import__('os')}}", {"goal": "G"})
        self.assertEqual(out, "G {{1+1}} {{__import__('os')}}")

    def test_changed_files_list_is_joined(self):
        out = templates.render_template(
            "Files: {{changed_files}}", templates.build_render_values(changed_files=["a.py", "b.py"])
        )
        self.assertEqual(out, "Files: a.py, b.py")


if __name__ == "__main__":
    unittest.main()
