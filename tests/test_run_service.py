"""Tests for the RunService prompt-loop orchestration and approval gate.

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
from autoprompt_runner.approvals import ApprovalStatus  # noqa: E402
from autoprompt_runner.models import AgentResult  # noqa: E402
from autoprompt_runner.runners.base import AgentRunner  # noqa: E402
from autoprompt_runner.services import RunService, RunServiceError  # noqa: E402
from autoprompt_runner.state import RunStatus  # noqa: E402


class FailingRunner(AgentRunner):
    """A runner that always reports a non-zero exit (for testing the FAILED path)."""

    @property
    def name(self) -> str:
        return "mock"

    def run(self, prompt: str) -> AgentResult:
        return AgentResult(
            stdout="", stderr="boom: something failed", exit_code=1,
            started_at="t0", finished_at="t1",
        )


class RunServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _service(self, providers=None):
        return RunService(self.db, providers=providers)

    def test_creates_pending_approval_by_default(self):
        report = self._service().start("Improve README", "mock", max_loops=3, require_approval=True)
        self.assertEqual(report.run_status, RunStatus.WAITING_APPROVAL.value)
        self.assertIsNotNone(report.approval_id)
        self.assertIsNotNone(report.next_prompt)
        self.assertEqual(len(storage.get_steps_for_run(self.db, report.run_id)), 1)  # one step only
        pending = storage.get_pending_approval(self.db, report.run_id)
        self.assertIsNotNone(pending)
        self.assertEqual(pending.status, ApprovalStatus.PENDING.value)

    def test_enforces_max_loops_in_no_approval_mode(self):
        report = self._service().start("p", "mock", max_loops=3, require_approval=False)
        self.assertEqual(report.run_status, RunStatus.DONE.value)
        self.assertEqual(len(storage.get_steps_for_run(self.db, report.run_id)), 3)  # exactly max_loops
        self.assertEqual(storage.get_run(self.db, report.run_id).status, RunStatus.DONE.value)

    def test_failure_marks_run_failed_and_stores_fix_prompt(self):
        report = self._service(
            providers={"mock": lambda workspace, timeout_seconds: FailingRunner()}
        ).start("p", "mock", max_loops=3, require_approval=False)
        self.assertEqual(report.run_status, RunStatus.FAILED.value)
        self.assertEqual(report.exit_code, 1)
        self.assertIsNotNone(report.next_prompt)
        steps = storage.get_steps_for_run(self.db, report.run_id)
        self.assertEqual(len(steps), 1)
        self.assertIsNotNone(steps[0].next_prompt)
        self.assertEqual(storage.get_run(self.db, report.run_id).status, RunStatus.FAILED.value)

    def test_approve_executes_next_step(self):
        svc = self._service()
        start = svc.start("p", "mock", max_loops=3, require_approval=True)
        report = svc.approve_and_continue(start.run_id)
        self.assertEqual(len(storage.get_steps_for_run(self.db, start.run_id)), 2)
        self.assertEqual(report.run_status, RunStatus.WAITING_APPROVAL.value)

    def test_approve_until_max_loops_marks_done(self):
        svc = self._service()
        start = svc.start("p", "mock", max_loops=2, require_approval=True)
        report = svc.approve_and_continue(start.run_id)  # second step reaches max_loops
        self.assertEqual(report.run_status, RunStatus.DONE.value)
        self.assertEqual(len(storage.get_steps_for_run(self.db, start.run_id)), 2)

    def test_reject_stops_run(self):
        svc = self._service()
        start = svc.start("p", "mock", max_loops=3, require_approval=True)
        report = svc.reject(start.run_id)
        self.assertEqual(report.run_status, RunStatus.STOPPED.value)
        self.assertEqual(storage.get_run(self.db, start.run_id).status, RunStatus.STOPPED.value)
        approvals = storage.list_approvals_for_run(self.db, start.run_id)
        self.assertEqual(approvals[-1].status, ApprovalStatus.REJECTED.value)

    def test_approve_without_pending_raises(self):
        svc = self._service()
        start = svc.start("p", "mock", max_loops=1, require_approval=False)  # ends DONE, no pending
        with self.assertRaises(RunServiceError):
            svc.approve_and_continue(start.run_id)

    def test_approve_missing_run_raises(self):
        with self.assertRaises(RunServiceError):
            self._service().approve_and_continue(999)


if __name__ == "__main__":
    unittest.main()
