"""Tests for project profiles (storage CRUD + default) and settings resolution.

Standard-library only (unittest + tempfile). Runnable via:
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

from autoprompt_runner import storage  # noqa: E402
from autoprompt_runner.models import Project  # noqa: E402
from autoprompt_runner.projects import (  # noqa: E402
    BUILTIN_MAX_LOOPS,
    BUILTIN_PROVIDER,
    BUILTIN_TIMEOUT_SECONDS,
    resolve_run_settings,
)


class ProjectStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        self.repo = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.repo)
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _add(self, name="P", provider="mock", max_loops=5, require_approval=True, timeout=1800, repo=None):
        return storage.create_project(
            self.db, name=name, repo_path=repo or self.repo, default_provider=provider,
            default_max_loops=max_loops, require_approval=require_approval, timeout_seconds=timeout,
        )

    def test_create_and_get_by_id_and_name(self):
        pid = self._add(name="FactoryColony", provider="claude-code", max_loops=5)
        by_id = storage.get_project_by_id(self.db, pid)
        by_name = storage.get_project_by_name(self.db, "FactoryColony")
        self.assertIsNotNone(by_id)
        self.assertIsNotNone(by_name)
        self.assertEqual(by_id.id, by_name.id)
        self.assertEqual(by_name.repo_path, self.repo)
        self.assertEqual(by_name.default_provider, "claude-code")
        self.assertEqual(by_name.default_max_loops, 5)
        self.assertTrue(by_name.require_approval)
        self.assertEqual(by_name.timeout_seconds, 1800)
        self.assertTrue(by_name.created_at)
        self.assertTrue(by_name.updated_at)

    def test_list_projects_ordered_by_name(self):
        self._add(name="B")
        self._add(name="A")
        self.assertEqual([p.name for p in storage.list_projects(self.db)], ["A", "B"])

    def test_get_missing_returns_none(self):
        storage.init_db(self.db)
        self.assertIsNone(storage.get_project_by_name(self.db, "nope"))
        self.assertIsNone(storage.get_project_by_id(self.db, 999))

    def test_update_project(self):
        pid = self._add(name="P", max_loops=1)
        storage.update_project(self.db, pid, default_max_loops=9, default_provider="claude-code")
        p = storage.get_project_by_id(self.db, pid)
        self.assertEqual(p.default_max_loops, 9)
        self.assertEqual(p.default_provider, "claude-code")

    def test_set_and_get_default(self):
        pid = self._add(name="P")
        self.assertIsNone(storage.get_default_project(self.db))
        storage.set_default_project(self.db, pid)
        default = storage.get_default_project(self.db)
        self.assertIsNotNone(default)
        self.assertEqual(default.id, pid)

    def test_delete_clears_default(self):
        pid = self._add(name="P")
        storage.set_default_project(self.db, pid)
        storage.delete_project(self.db, pid)
        self.assertIsNone(storage.get_project_by_id(self.db, pid))
        self.assertIsNone(storage.get_default_project(self.db))

    def test_delete_non_default_keeps_existing_default(self):
        a = self._add(name="A")
        b = self._add(name="B")
        storage.set_default_project(self.db, a)
        storage.delete_project(self.db, b)
        default = storage.get_default_project(self.db)
        self.assertIsNotNone(default)
        self.assertEqual(default.id, a)

    def test_delete_does_not_touch_filesystem(self):
        marker = os.path.join(self.repo, "keep.txt")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("content")
        pid = self._add(name="P")
        storage.delete_project(self.db, pid)
        self.assertTrue(os.path.exists(marker))
        self.assertTrue(os.path.isdir(self.repo))


class ResolveRunSettingsTests(unittest.TestCase):
    def _project(self, **overrides):
        fields = dict(
            id=1, name="P", repo_path="/repo", default_provider="claude-code",
            default_max_loops=5, require_approval=True, timeout_seconds=900,
            created_at="t", updated_at="t",
        )
        fields.update(overrides)
        return Project(**fields)

    def test_builtin_defaults_without_project(self):
        settings = resolve_run_settings(None)
        self.assertEqual(settings.provider, BUILTIN_PROVIDER)
        self.assertEqual(settings.max_loops, BUILTIN_MAX_LOOPS)
        self.assertEqual(settings.timeout_seconds, BUILTIN_TIMEOUT_SECONDS)
        self.assertTrue(settings.require_approval)
        self.assertIsNone(settings.workspace)

    def test_uses_project_defaults(self):
        settings = resolve_run_settings(self._project())
        self.assertEqual(settings.provider, "claude-code")
        self.assertEqual(settings.max_loops, 5)
        self.assertEqual(settings.timeout_seconds, 900)
        self.assertTrue(settings.require_approval)
        self.assertEqual(settings.workspace, "/repo")  # claude-code workspace from repo_path

    def test_explicit_args_override_project(self):
        settings = resolve_run_settings(
            self._project(), provider="mock", max_loops=2, timeout_seconds=10, workspace="/other"
        )
        self.assertEqual(settings.provider, "mock")
        self.assertEqual(settings.max_loops, 2)
        self.assertEqual(settings.timeout_seconds, 10)
        self.assertEqual(settings.workspace, "/other")

    def test_no_approval_forces_false(self):
        settings = resolve_run_settings(self._project(require_approval=True), no_approval=True)
        self.assertFalse(settings.require_approval)

    def test_mock_provider_has_no_workspace_fallback(self):
        settings = resolve_run_settings(self._project(default_provider="mock"))
        self.assertEqual(settings.provider, "mock")
        self.assertIsNone(settings.workspace)


if __name__ == "__main__":
    unittest.main()
