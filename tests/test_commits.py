"""Tests for run result review and the local commit workflow (autoprompt_runner.commits).

Standard-library only (unittest + tempfile + subprocess). Uses a real local Git repo; no
network and no external agent. Runnable via:
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

from autoprompt_runner import commits, git_utils, storage  # noqa: E402
from autoprompt_runner.state import RunStatus  # noqa: E402

_GIT_ENV = ["-c", "user.email=t@example.com", "-c", "user.name=test"]


def _git(path, *args):
    return subprocess.run(["git", *_GIT_ENV, *args], cwd=path, capture_output=True, text=True)


def _commit_count(path):
    return int(_git(path, "rev-list", "--count", "HEAD").stdout.strip() or "0")


class CommitTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.ws = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.ws)
        _git(self.ws, "init", "-q")
        self._write("README.md", "seed\n")
        _git(self.ws, "add", ".")
        _git(self.ws, "commit", "-q", "-m", "init")
        self.run_id = self._done_run()

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        with open(os.path.join(self.ws, name), "w", encoding="utf-8") as handle:
            handle.write(content)

    def _done_run(self, prompt="Add input validation to the signup endpoint", workspace=None):
        rid = storage.create_run(
            self.db, root_prompt=prompt, provider="mock", max_loops=1, require_approval=False,
            workspace=workspace if workspace is not None else self.ws,
        )
        storage.update_run_status(self.db, rid, RunStatus.RUNNING.value)
        storage.update_run_status(self.db, rid, RunStatus.DONE.value)
        return rid


class ReviewTests(CommitTestCase):
    def test_review_for_successful_run_with_changes(self):
        self._write("README.md", "seed\nedited\n")
        review = commits.build_run_commit_review(self.db, self.run_id)
        self.assertTrue(review.ready)
        self.assertEqual(review.blockers, [])
        self.assertIn("README.md", review.changed_files)
        self.assertTrue(review.git_diff_stat.strip())

    def test_review_rejects_non_git_workspace(self):
        plain = os.path.join(self._tmp.name, "plain")
        os.makedirs(plain)
        rid = self._done_run(workspace=plain)
        review = commits.build_run_commit_review(self.db, rid)
        self.assertFalse(review.ready)
        self.assertTrue(any("not a git repository" in b for b in review.blockers))

    def test_review_rejects_no_changes(self):
        review = commits.build_run_commit_review(self.db, self.run_id)  # clean repo
        self.assertFalse(review.ready)
        self.assertIn("no changed files to commit", review.blockers)

    def test_secret_like_files_are_blockers(self):
        self._write(".env", "SECRET=1\n")
        review = commits.build_run_commit_review(self.db, self.run_id)
        self.assertFalse(review.ready)
        self.assertTrue(any("secret-like" in b for b in review.blockers))

    def test_review_missing_run_raises_not_found(self):
        with self.assertRaises(commits.CommitError) as ctx:
            commits.build_run_commit_review(self.db, 9999)
        self.assertEqual(ctx.exception.kind, "not_found")

    def test_failed_run_blocked_unless_allow_failed(self):
        rid = storage.create_run(
            self.db, root_prompt="x", provider="mock", max_loops=1, require_approval=False, workspace=self.ws
        )
        storage.update_run_status(self.db, rid, RunStatus.RUNNING.value)
        storage.update_run_status(self.db, rid, RunStatus.FAILED.value)
        self._write("README.md", "seed\nx\n")
        self.assertTrue(any("run failed" in b for b in commits.build_run_commit_review(self.db, rid).blockers))
        self.assertNotIn(
            "run failed",
            " ".join(commits.build_run_commit_review(self.db, rid, allow_failed=True).blockers),
        )


class MessageTests(CommitTestCase):
    def test_generate_compact_message(self):
        message = commits.generate_commit_message(self.db, self.run_id)
        subject = message.splitlines()[0]
        self.assertEqual(subject, "Add input validation to the signup endpoint")
        self.assertLessEqual(len(subject), 72)
        self.assertIn(f"Run #{self.run_id}", message)
        self.assertIn("mock", message)

    def test_long_prompt_subject_is_capped(self):
        rid = self._done_run(prompt="word " * 40)  # ~200 chars
        subject = commits.generate_commit_message(self.db, rid).splitlines()[0]
        self.assertLessEqual(len(subject), 72)


class ApplyTests(CommitTestCase):
    def test_propose_creates_record(self):
        self._write("README.md", "seed\nedited\n")
        record = commits.propose_commit(self.db, self.run_id)
        self.assertEqual(record.status, storage.COMMIT_PROPOSED)
        self.assertTrue(record.commit_message)
        self.assertEqual(commits.list_commits(self.db, self.run_id)[0].id, record.id)

    def test_apply_refuses_without_confirm(self):
        self._write("README.md", "seed\nedited\n")
        before = _commit_count(self.ws)
        with self.assertRaises(commits.CommitError) as ctx:
            commits.commit_run_changes(self.db, self.run_id, confirm=False)
        self.assertEqual(ctx.exception.kind, "not_confirmed")
        self.assertEqual(_commit_count(self.ws), before)  # no commit created

    def test_apply_creates_local_commit(self):
        self._write("README.md", "seed\nedited\n")
        before = _commit_count(self.ws)
        result = commits.commit_run_changes(self.db, self.run_id, confirm=True)
        self.assertTrue(result.committed)
        self.assertEqual(result.status, storage.COMMIT_COMMITTED)
        self.assertEqual(_commit_count(self.ws), before + 1)  # exactly one new local commit

    def test_apply_stores_commit_hash(self):
        self._write("README.md", "seed\nedited\n")
        result = commits.commit_run_changes(self.db, self.run_id, confirm=True)
        head = git_utils.git_get_last_commit_hash(self.ws)
        self.assertEqual(result.commit_hash, head)
        record = commits.get_commit(self.db, result.commit_id)
        self.assertEqual(record.status, storage.COMMIT_COMMITTED)
        self.assertEqual(record.commit_hash, head)

    def test_apply_no_changes_raises(self):
        with self.assertRaises(commits.CommitError) as ctx:
            commits.commit_run_changes(self.db, self.run_id, confirm=True)  # clean repo
        self.assertEqual(ctx.exception.kind, "no_changes")

    def test_apply_only_stages_selected_files(self):
        self._write("README.md", "seed\nedited\n")
        self._write("other.txt", "new file\n")
        result = commits.commit_run_changes(self.db, self.run_id, confirm=True, files=["README.md"])
        self.assertTrue(result.committed)
        self.assertEqual(result.changed_files, ["README.md"])
        # other.txt was not committed -> still an untracked change.
        self.assertIn("other.txt", git_utils.get_changed_files(self.ws))

    def test_secret_files_block_apply(self):
        self._write(".env", "SECRET=1\n")
        with self.assertRaises(commits.CommitError) as ctx:
            commits.commit_run_changes(self.db, self.run_id, confirm=True)
        self.assertEqual(ctx.exception.kind, "blocked")


if __name__ == "__main__":
    unittest.main()
