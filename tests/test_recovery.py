"""Tests for the failure recovery workflow (autoprompt_runner.recovery)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import recovery, storage  # noqa: E402
from autoprompt_runner.artifacts import ArtifactType  # noqa: E402
from autoprompt_runner.state import RunStatus  # noqa: E402


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _failed_run(self, stderr="AssertionError: expected 400 got 500", stdout="2 passed 1 failed", changed="src/signup.py"):
        run_id = storage.create_run(
            self.db, root_prompt="Fix the signup validation", provider="mock", max_loops=2, require_approval=False
        )
        step_id = storage.create_step(
            self.db, run_id, 0, "run the tests", "FAILED", stdout=stdout, stderr=stderr, exit_code=1,
        )
        if changed:
            storage.create_artifact(self.db, run_id, ArtifactType.CHANGED_FILES.value, content=changed, step_id=step_id)
        storage.create_artifact(
            self.db, run_id, ArtifactType.GIT_DIFF_STAT.value, content="1 file changed, 3 insertions", step_id=step_id
        )
        storage.update_run_status(self.db, run_id, RunStatus.FAILED.value)
        return run_id, step_id

    def _done_run(self):
        run_id = storage.create_run(self.db, root_prompt="ok", provider="mock", max_loops=1, require_approval=False)
        storage.update_run_status(self.db, run_id, RunStatus.RUNNING.value)
        storage.update_run_status(self.db, run_id, RunStatus.DONE.value)
        return run_id

    def test_find_failed_step(self):
        run_id, step_id = self._failed_run()
        step = recovery.find_failed_step(self.db, run_id)
        self.assertIsNotNone(step)
        self.assertEqual(step.id, step_id)

    def test_build_failure_context(self):
        run_id, step_id = self._failed_run()
        ctx = recovery.build_failure_context(self.db, run_id)
        self.assertEqual(ctx.failed_step_id, step_id)
        self.assertEqual(ctx.exit_code, 1)
        self.assertIn("AssertionError", ctx.stderr_preview)
        self.assertEqual(ctx.changed_files, ["src/signup.py"])
        self.assertEqual(ctx.provider, "mock")
        self.assertIn("signup", ctx.root_prompt)

    def test_propose_recovery_for_failed_run(self):
        run_id, step_id = self._failed_run()
        attempt = recovery.propose_recovery(self.db, run_id, reason="tests failing")
        self.assertEqual(attempt.status, recovery.RECOVERY_PROPOSED)
        self.assertEqual(attempt.source_run_id, run_id)
        self.assertEqual(attempt.failed_step_id, step_id)
        self.assertIsNone(attempt.recovery_run_id)

    def test_propose_for_non_failed_run_rejected(self):
        done = self._done_run()
        with self.assertRaises(recovery.RecoveryError) as ctx:
            recovery.propose_recovery(self.db, done)
        self.assertEqual(ctx.exception.kind, "not_failed")

    def test_propose_missing_run(self):
        with self.assertRaises(recovery.RecoveryError) as ctx:
            recovery.propose_recovery(self.db, 9999)
        self.assertEqual(ctx.exception.kind, "not_found")

    def test_recovery_prompt_uses_stderr_when_available(self):
        run_id, _ = self._failed_run(stderr="SegmentationFault in widget.c", stdout="some stdout")
        attempt = recovery.propose_recovery(self.db, run_id)
        self.assertIn("stderr as the primary source", attempt.recovery_prompt)
        self.assertIn("SegmentationFault", attempt.recovery_prompt)

    def test_recovery_prompt_falls_back_to_stdout(self):
        run_id, _ = self._failed_run(stderr="", stdout="boom in output", changed=None)
        attempt = recovery.propose_recovery(self.db, run_id)
        self.assertNotIn("stderr as the primary source", attempt.recovery_prompt)
        self.assertIn("stdout", attempt.recovery_prompt)

    def test_recovery_prompt_does_not_include_huge_content(self):
        huge = "X" * 5000
        run_id, _ = self._failed_run(stderr=huge)
        attempt = recovery.propose_recovery(self.db, run_id)
        # The prompt carries only a short preview, never the full 5000-char stderr.
        self.assertLess(len(attempt.recovery_prompt), 1000)
        self.assertNotIn("X" * 1000, attempt.recovery_prompt)

    def test_approve_recovery(self):
        run_id, _ = self._failed_run()
        attempt = recovery.propose_recovery(self.db, run_id)
        approved = recovery.approve_recovery(self.db, attempt.id)
        self.assertEqual(approved.status, recovery.RECOVERY_APPROVED)
        self.assertIsNotNone(approved.decided_at)

    def test_reject_recovery(self):
        run_id, _ = self._failed_run()
        attempt = recovery.propose_recovery(self.db, run_id)
        rejected = recovery.reject_recovery(self.db, attempt.id, reason="not needed")
        self.assertEqual(rejected.status, recovery.RECOVERY_REJECTED)
        self.assertEqual(rejected.reason, "not needed")

    def test_execute_recovery_creates_linked_run(self):
        run_id, _ = self._failed_run()
        attempt = recovery.propose_recovery(self.db, run_id)
        recovery.approve_recovery(self.db, attempt.id)
        result = recovery.execute_recovery(self.db, attempt.id, queued=False)
        self.assertIsNotNone(result.recovery_run_id)
        self.assertEqual(result.attempt.status, recovery.RECOVERY_EXECUTED)
        self.assertEqual(result.attempt.recovery_run_id, result.recovery_run_id)
        # A new run was created and linked; the source run is untouched (still FAILED).
        self.assertIsNotNone(storage.get_run(self.db, result.recovery_run_id))
        self.assertNotEqual(result.recovery_run_id, run_id)
        self.assertEqual(storage.get_run(self.db, run_id).status, RunStatus.FAILED.value)

    def test_execute_recovery_reuses_source_settings(self):
        run_id = storage.create_run(
            self.db, root_prompt="Fix", provider="mock", max_loops=4, require_approval=False, workspace=None
        )
        storage.create_step(self.db, run_id, 0, "run", "FAILED", stderr="boom", exit_code=1)
        storage.update_run_status(self.db, run_id, RunStatus.FAILED.value)
        attempt = recovery.propose_recovery(self.db, run_id)
        result = recovery.execute_recovery(self.db, attempt.id)
        recovery_run = storage.get_run(self.db, result.recovery_run_id)
        self.assertEqual(recovery_run.provider, "mock")
        self.assertEqual(recovery_run.max_loops, 4)

    def test_execute_queued_attaches_run_immediately(self):
        run_id, _ = self._failed_run()
        attempt = recovery.propose_recovery(self.db, run_id)
        result = recovery.execute_recovery(self.db, attempt.id, queued=True)
        self.assertTrue(result.queued)
        self.assertIsNotNone(result.recovery_run_id)
        self.assertEqual(storage.get_recovery_attempt(self.db, attempt.id).recovery_run_id, result.recovery_run_id)

    def test_execute_rejected_recovery_raises(self):
        run_id, _ = self._failed_run()
        attempt = recovery.propose_recovery(self.db, run_id)
        recovery.reject_recovery(self.db, attempt.id)
        with self.assertRaises(recovery.RecoveryError) as ctx:
            recovery.execute_recovery(self.db, attempt.id)
        self.assertEqual(ctx.exception.kind, "rejected")

    def test_list_recoveries_for_run(self):
        run_id, _ = self._failed_run()
        recovery.propose_recovery(self.db, run_id)
        recovery.propose_recovery(self.db, run_id)
        items = recovery.list_recoveries_for_run(self.db, run_id)
        self.assertEqual(len(items), 2)
        self.assertGreater(items[0].id, items[1].id)  # newest first


if __name__ == "__main__":
    unittest.main()
