"""Tests for the read-only Git helpers.

These create real temporary Git repositories (git is required) and exercise the
read-only helpers. The setup uses git init/add/commit as test scaffolding only; the
production helpers under test never mutate a repository. Standard-library only.
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

from autoprompt_runner import git_utils  # noqa: E402


class GitUtilsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", *args],
            cwd=self.repo, capture_output=True, text=True,
        )

    def _write(self, name, text):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def _init_with_commit(self):
        self._git("init", "-q")
        self._write("tracked.txt", "hello\n")
        self._git("add", "tracked.txt")
        self._git("commit", "-q", "-m", "init")

    def test_is_git_repository_false_for_plain_dir(self):
        self.assertFalse(git_utils.is_git_repository(self.repo))

    def test_is_git_repository_true_after_init(self):
        self._git("init", "-q")
        self.assertTrue(git_utils.is_git_repository(self.repo))

    def test_get_git_status_lists_untracked(self):
        self._git("init", "-q")
        self._write("new.txt", "x")
        self.assertIn("new.txt", git_utils.get_git_status(self.repo))

    def test_get_git_diff_after_change(self):
        self._init_with_commit()
        self._write("tracked.txt", "hello\nmore\n")
        diff = git_utils.get_git_diff(self.repo)
        self.assertIn("tracked.txt", diff)
        self.assertIn("more", diff)

    def test_get_git_diff_stat_after_change(self):
        self._init_with_commit()
        self._write("tracked.txt", "hello\nmore\n")
        self.assertIn("tracked.txt", git_utils.get_git_diff_stat(self.repo))

    def test_get_changed_files_detects_changes(self):
        self._init_with_commit()
        self._write("tracked.txt", "hello\nmore\n")
        self._write("untracked.txt", "y")
        changed = git_utils.get_changed_files(self.repo)
        self.assertIn("tracked.txt", changed)
        self.assertIn("untracked.txt", changed)

    def test_run_git_command_readonly_ok(self):
        self._git("init", "-q")
        result = git_utils.run_git_command(self.repo, ["rev-parse", "--is-inside-work-tree"])
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout.strip(), "true")

    def test_run_git_command_rejects_destructive(self):
        for destructive in (["commit", "-m", "x"], ["push"], ["reset", "--hard"], ["checkout", "."], ["clean", "-fd"]):
            with self.assertRaises(ValueError):
                git_utils.run_git_command(self.repo, destructive)


if __name__ == "__main__":
    unittest.main()
