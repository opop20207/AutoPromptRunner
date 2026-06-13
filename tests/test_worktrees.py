"""Tests for Git worktree helpers and worktree record storage (standard library only).

Runnable via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import storage, worktrees  # noqa: E402

_GIT_ENV = ["-c", "user.email=t@example.com", "-c", "user.name=test"]


def _init_repo(path: str) -> None:
    """Create a Git repo with one commit so it has a HEAD to branch worktrees from."""
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", *_GIT_ENV, "init", "-q"], cwd=path, capture_output=True, text=True)
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as handle:
        handle.write("seed\n")
    subprocess.run(["git", *_GIT_ENV, "add", "."], cwd=path, capture_output=True, text=True)
    subprocess.run(["git", *_GIT_ENV, "commit", "-q", "-m", "init"], cwd=path, capture_output=True, text=True)


class ValidationTests(unittest.TestCase):
    def test_valid_worktree_names(self):
        for name in ("ui-session", "feature_1", "a.b-c", "X"):
            self.assertEqual(worktrees.validate_worktree_name(name), name)

    def test_invalid_worktree_names(self):
        for name in ("", "   ", ".", "..", "a/b", "a\\b", "a b", "a:b"):
            with self.assertRaises(worktrees.WorktreeError):
                worktrees.validate_worktree_name(name)

    def test_valid_branch_names(self):
        for branch in ("autoprompt/ui-session", "main", "feature/x_1", "v1.2"):
            self.assertEqual(worktrees.validate_branch_name(branch), branch)

    def test_invalid_branch_names(self):
        for branch in ("", "a b", "a..b", "-lead", "/lead", "trail/", "a//b",
                       "x.lock", "@", "a~b", "a^b", "a:b", "a?b", "a*b", "a[b", "feat@{0}"):
            with self.assertRaises(worktrees.WorktreeError):
                worktrees.validate_branch_name(branch)


class PathContainmentTests(unittest.TestCase):
    def test_inside(self):
        self.assertTrue(worktrees.is_path_inside_parent(os.path.join("a", "b", "c"), os.path.join("a", "b")))

    def test_equal_is_not_inside(self):
        self.assertFalse(worktrees.is_path_inside_parent(os.path.join("a", "b"), os.path.join("a", "b")))

    def test_sibling_prefix_not_inside(self):
        self.assertFalse(worktrees.is_path_inside_parent(os.path.join("a", "bc"), os.path.join("a", "b")))

    def test_outside(self):
        self.assertFalse(worktrees.is_path_inside_parent(os.path.join("x", "y"), os.path.join("a", "b")))

    def test_build_path_inside_root(self):
        root = os.path.abspath("root")
        path = worktrees.build_worktree_path(os.path.join(root, "proj"), "wt")
        self.assertTrue(worktrees.is_path_inside_parent(path, root))

    def test_build_path_rejects_bad_name(self):
        with self.assertRaises(worktrees.WorktreeError):
            worktrees.build_worktree_path("root", "../escape")

    def test_prepare_path_stays_inside_root(self):
        db_path = os.path.join("state", "autoprompt.db")
        path = worktrees.prepare_worktree_path(db_path, "My Project!", "ui-session")
        root = worktrees.default_worktrees_root(db_path)
        self.assertTrue(worktrees.is_path_inside_parent(path, root))


class GitWorktreeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = os.path.join(self._tmp.name, "repo")
        _init_repo(self.repo)
        self.root = os.path.join(self._tmp.name, "worktrees")

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_list_remove(self):
        path = os.path.join(self.root, "wt1")
        entry = worktrees.create_git_worktree(self.repo, path, "autoprompt/wt1")
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(entry.branch, "autoprompt/wt1")
        listed_paths = [os.path.abspath(e.path) for e in worktrees.list_git_worktrees(self.repo)]
        self.assertIn(os.path.abspath(path), listed_paths)
        worktrees.remove_git_worktree(self.repo, path)
        self.assertFalse(os.path.isdir(path))

    def test_create_rejects_existing_path(self):
        path = os.path.join(self.root, "wt2")
        os.makedirs(path)
        with self.assertRaises(worktrees.WorktreeError):
            worktrees.create_git_worktree(self.repo, path, "autoprompt/wt2")

    def test_create_with_base_branch(self):
        path = os.path.join(self.root, "wt3")
        entry = worktrees.create_git_worktree(self.repo, path, "autoprompt/wt3", base_branch="HEAD")
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(entry.branch, "autoprompt/wt3")

    def test_run_git_refuses_unlisted_command(self):
        with self.assertRaises(worktrees.WorktreeError):
            worktrees._run_git(self.repo, ["status"])  # not in the allowlist


class WorktreeRecordTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.project_id = storage.create_project(
            self.db, name="P", repo_path=self._tmp.name, default_provider="mock",
            default_max_loops=1, require_approval=True, timeout_seconds=1800,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _create(self, name="ui-session"):
        return storage.create_worktree_record(
            self.db, project_id=self.project_id, name=name, branch=f"autoprompt/{name}",
            path=os.path.join(self._tmp.name, "worktrees", name), base_branch="main",
            status=worktrees.WORKTREE_ACTIVE,
        )

    def test_create_list_get(self):
        wid = self._create()
        self.assertIsInstance(wid, int)
        self.assertIn("ui-session", [w.name for w in storage.list_worktrees(self.db)])
        self.assertIn("ui-session", [w.name for w in storage.list_worktrees_for_project(self.db, self.project_id)])
        by_id = storage.get_worktree_by_id(self.db, wid)
        by_name = storage.get_worktree_by_name(self.db, "ui-session")
        self.assertEqual(by_id.id, wid)
        self.assertEqual(by_name.branch, "autoprompt/ui-session")
        self.assertEqual(by_name.status, worktrees.WORKTREE_ACTIVE)

    def test_archive_status_update(self):
        wid = self._create()
        storage.update_worktree_status(self.db, wid, worktrees.WORKTREE_ARCHIVED)
        self.assertEqual(storage.get_worktree_by_id(self.db, wid).status, worktrees.WORKTREE_ARCHIVED)

    def test_delete_record(self):
        wid = self._create()
        storage.delete_worktree_record(self.db, wid)
        self.assertIsNone(storage.get_worktree_by_name(self.db, "ui-session"))


if __name__ == "__main__":
    unittest.main()
