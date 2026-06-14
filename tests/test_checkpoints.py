"""Tests for run checkpoints and rollback (autoprompt_runner.checkpoints).

Standard-library only (unittest + tempfile + subprocess). Uses a real local Git repo (no
network, no external agent). Runnable via:
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

from autoprompt_runner import checkpoints, git_utils, storage  # noqa: E402

_GIT_ENV = ["-c", "user.email=t@example.com", "-c", "user.name=test"]


def _git(path, *args):
    return subprocess.run(["git", *_GIT_ENV, *args], cwd=path, capture_output=True, text=True)


class CheckpointTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.ws = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.ws)
        _git(self.ws, "init", "-q")
        self._write("f.txt", "one\n")
        _git(self.ws, "add", ".")
        _git(self.ws, "commit", "-q", "-m", "init")
        self.run_id = storage.create_run(
            self.db, root_prompt="p", provider="mock", max_loops=1, require_approval=False, workspace=self.ws
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        with open(os.path.join(self.ws, name), "w", encoding="utf-8") as handle:
            handle.write(content)

    def _read(self, name):
        with open(os.path.join(self.ws, name), encoding="utf-8") as handle:
            return handle.read()


class CreateTests(CheckpointTestCase):
    def test_skipped_for_missing_workspace(self):
        cp = checkpoints.create_checkpoint(self.db, self.run_id, None, None)
        self.assertEqual(cp.status, storage.CHECKPOINT_SKIPPED)
        self.assertIsNone(cp.git_head_before)
        self.assertTrue(cp.restore_error)  # skip reason stored

    def test_skipped_for_nonexistent_workspace(self):
        cp = checkpoints.create_checkpoint(self.db, self.run_id, None, os.path.join(self._tmp.name, "nope"))
        self.assertEqual(cp.status, storage.CHECKPOINT_SKIPPED)

    def test_skipped_for_non_git_workspace(self):
        plain = os.path.join(self._tmp.name, "plain")
        os.makedirs(plain)
        cp = checkpoints.create_checkpoint(self.db, self.run_id, None, plain)
        self.assertEqual(cp.status, storage.CHECKPOINT_SKIPPED)
        self.assertIn("not a git", (cp.restore_error or "").lower())

    def test_created_for_git_workspace(self):
        cp = checkpoints.create_checkpoint(self.db, self.run_id, None, self.ws)
        self.assertEqual(cp.status, storage.CHECKPOINT_CREATED)
        # No commit/tag is created; the captured HEAD itself is the rollback ref.
        self.assertEqual(cp.checkpoint_ref, cp.git_head_before)

    def test_captures_head_and_branch(self):
        cp = checkpoints.create_checkpoint(self.db, self.run_id, None, self.ws)
        self.assertEqual(cp.git_head_before, git_utils.get_git_head(self.ws))
        self.assertIn(cp.git_branch_before, ("master", "main"))
        self.assertFalse(checkpoints.detect_preexisting_dirty_state(cp))  # clean repo

    def test_dirty_workspace_warning_captured(self):
        self._write("f.txt", "uncommitted change\n")  # dirty BEFORE the checkpoint
        cp = checkpoints.create_checkpoint(self.db, self.run_id, None, self.ws)
        self.assertEqual(cp.status, storage.CHECKPOINT_CREATED)  # still CREATED, not failed
        self.assertTrue((cp.git_status_before or "").strip())
        self.assertTrue(checkpoints.detect_preexisting_dirty_state(cp))


class RollbackTests(CheckpointTestCase):
    def _clean_checkpoint(self):
        return checkpoints.create_checkpoint(self.db, self.run_id, None, self.ws)

    def test_rollback_plan_does_not_modify_files(self):
        cp = self._clean_checkpoint()
        self._write("f.txt", "AGENT EDIT\n")
        plan = checkpoints.build_rollback_plan(self.db, cp.id)
        self.assertTrue(plan.can_rollback)
        self.assertTrue(plan.current_dirty)
        self.assertEqual(self._read("f.txt"), "AGENT EDIT\n")  # plan changed nothing
        self.assertEqual(checkpoints.get_checkpoint(self.db, cp.id).status, storage.CHECKPOINT_CREATED)

    def test_rollback_refuses_without_confirm(self):
        cp = self._clean_checkpoint()
        self._write("f.txt", "AGENT EDIT\n")
        with self.assertRaises(checkpoints.CheckpointError) as ctx:
            checkpoints.rollback_checkpoint(self.db, cp.id, confirm=False)
        self.assertEqual(ctx.exception.kind, "not_confirmed")
        self.assertEqual(self._read("f.txt"), "AGENT EDIT\n")  # nothing was reset

    def test_rollback_marks_restored_on_success(self):
        cp = self._clean_checkpoint()
        self._write("f.txt", "AGENT EDIT\n")
        result = checkpoints.rollback_checkpoint(self.db, cp.id, confirm=True)
        self.assertTrue(result.restored)
        self.assertEqual(result.status, storage.CHECKPOINT_RESTORED)
        self.assertEqual(self._read("f.txt"), "one\n")  # workspace restored
        restored = checkpoints.get_checkpoint(self.db, cp.id)
        self.assertEqual(restored.status, storage.CHECKPOINT_RESTORED)
        self.assertTrue(restored.restored_at)

    def test_rollback_records_safety_artifact(self):
        cp = self._clean_checkpoint()
        self._write("f.txt", "AGENT EDIT\n")
        checkpoints.rollback_checkpoint(self.db, cp.id, confirm=True)
        types = [a.type for a in storage.list_artifacts_for_run(self.db, self.run_id)]
        self.assertIn("safety_warning", types)  # warning recorded before the reset
        self.assertIn("checkpoint_rollback", types)

    def test_rollback_refuses_unsafe_dirty_unless_force(self):
        # A pre-existing uncommitted file (not created by the run) makes rollback unsafe.
        self._write("pre.txt", "preexisting work\n")
        cp = checkpoints.create_checkpoint(self.db, self.run_id, None, self.ws)
        self.assertTrue(checkpoints.detect_preexisting_dirty_state(cp))
        plan = checkpoints.build_rollback_plan(self.db, cp.id)
        self.assertTrue(plan.requires_force)
        self.assertFalse(plan.safe)
        with self.assertRaises(checkpoints.CheckpointError) as ctx:
            checkpoints.rollback_checkpoint(self.db, cp.id, confirm=True, force=False)
        self.assertEqual(ctx.exception.kind, "unsafe")
        # With explicit force it proceeds and is marked RESTORED.
        result = checkpoints.rollback_checkpoint(self.db, cp.id, confirm=True, force=True)
        self.assertTrue(result.restored)
        self.assertEqual(checkpoints.get_checkpoint(self.db, cp.id).status, storage.CHECKPOINT_RESTORED)

    def test_rollback_missing_checkpoint_raises_not_found(self):
        with self.assertRaises(checkpoints.CheckpointError) as ctx:
            checkpoints.rollback_checkpoint(self.db, 9999, confirm=True)
        self.assertEqual(ctx.exception.kind, "not_found")

    def test_skipped_checkpoint_cannot_be_rolled_back(self):
        plain = os.path.join(self._tmp.name, "plain")
        os.makedirs(plain)
        cp = checkpoints.create_checkpoint(self.db, self.run_id, None, plain)
        plan = checkpoints.build_rollback_plan(self.db, cp.id)
        self.assertFalse(plan.can_rollback)
        with self.assertRaises(checkpoints.CheckpointError) as ctx:
            checkpoints.rollback_checkpoint(self.db, cp.id, confirm=True)
        self.assertEqual(ctx.exception.kind, "unsafe")

    def test_detect_post_run_dirty_state(self):
        cp = self._clean_checkpoint()
        self.assertFalse(checkpoints.detect_post_run_dirty_state(cp))  # clean now
        self._write("f.txt", "AGENT EDIT\n")
        self.assertTrue(checkpoints.detect_post_run_dirty_state(cp))


class QueryTests(CheckpointTestCase):
    def test_list_and_latest(self):
        first = checkpoints.create_checkpoint(self.db, self.run_id, None, self.ws)
        second = checkpoints.create_checkpoint(self.db, self.run_id, None, self.ws)
        listed = checkpoints.list_checkpoints(self.db, self.run_id)
        self.assertEqual([c.id for c in listed], [second.id, first.id])  # newest first
        self.assertEqual(checkpoints.get_latest_checkpoint(self.db, self.run_id).id, second.id)

    def test_git_reset_hard_requires_confirm(self):
        # The destructive helper itself refuses without an explicit confirm guard.
        with self.assertRaises(ValueError):
            git_utils.git_reset_hard(self.ws, "HEAD", confirm=False)


if __name__ == "__main__":
    unittest.main()
