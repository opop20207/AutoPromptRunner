"""Tests for RunService: prompt-loop orchestration, approval gate, artifacts, context.

Standard-library only (unittest + tempfile + subprocess). Runnable via:
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

from autoprompt_runner import storage  # noqa: E402
from autoprompt_runner.approvals import ApprovalStatus  # noqa: E402
from autoprompt_runner.models import AgentResult, NextPrompt  # noqa: E402
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
            stdout="", stderr="boom: process crashed", exit_code=1,
            started_at="t0", finished_at="t1",
        )


class RecordingGenerator:
    """A PromptGenerator stand-in that records the contexts it receives."""

    def __init__(self):
        self.contexts = []

    def generate(self, context):
        self.contexts.append(context)
        return NextPrompt(prompt="recorded next prompt", kind="continue", loop_index=context.loop_index + 1)


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


class GitArtifactCaptureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _init_git_repo(self):
        repo = os.path.join(self._tmp.name, "repo")
        os.makedirs(repo)
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=test", "init", "-q"],
            cwd=repo, capture_output=True, text=True,
        )
        return repo

    def _types(self, run_id):
        return {a.type for a in storage.list_artifacts_for_run(self.db, run_id)}

    def test_stores_git_artifacts_when_workspace_is_git_repo(self):
        repo = self._init_git_repo()
        report = RunService(self.db).start("p", "mock", max_loops=1, require_approval=False, workspace=repo)
        types = self._types(report.run_id)
        for expected in (
            "git_status_before", "git_status_after", "git_diff",
            "git_diff_stat", "changed_files", "runner_stdout", "runner_stderr",
        ):
            self.assertIn(expected, types)
        self.assertNotIn("git_skipped", types)

    def test_skips_git_artifacts_when_workspace_is_none(self):
        report = RunService(self.db).start("p", "mock", max_loops=1, require_approval=False, workspace=None)
        types = self._types(report.run_id)
        self.assertEqual(report.run_status, RunStatus.DONE.value)  # not failed
        self.assertIn("git_skipped", types)
        self.assertIn("runner_stdout", types)
        self.assertNotIn("git_status_before", types)

    def test_non_git_workspace_does_not_fail_run(self):
        plain = os.path.join(self._tmp.name, "plain")
        os.makedirs(plain)
        report = RunService(self.db).start("p", "mock", max_loops=1, require_approval=False, workspace=plain)
        self.assertEqual(report.run_status, RunStatus.DONE.value)
        self.assertIn("git_skipped", self._types(report.run_id))


class PromptContextTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _git_repo_with_change(self):
        repo = os.path.join(self._tmp.name, "repo")
        os.makedirs(repo)

        def git(*args):
            subprocess.run(
                ["git", "-c", "user.email=t@example.com", "-c", "user.name=test", *args],
                cwd=repo, capture_output=True, text=True,
            )

        git("init", "-q")
        path = os.path.join(repo, "tracked.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("a\n")
        git("add", "tracked.txt")
        git("commit", "-q", "-m", "init")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("b\n")  # modify so it shows in diff stat and changed files
        return repo

    def test_passes_git_context_to_generator(self):
        repo = self._git_repo_with_change()
        recorder = RecordingGenerator()
        report = RunService(self.db, generator=recorder).start(
            "Improve project", "mock", max_loops=2, require_approval=True, workspace=repo
        )
        self.assertEqual(report.run_status, RunStatus.WAITING_APPROVAL.value)
        self.assertEqual(len(recorder.contexts), 1)
        context = recorder.contexts[0]
        self.assertEqual(context.provider, "mock")
        self.assertEqual(context.workspace, repo)
        self.assertEqual(context.max_loops, 2)
        self.assertEqual(context.loop_index, 0)
        self.assertIn("tracked.txt", context.changed_files)
        self.assertIn("tracked.txt", context.git_diff_stat)

    def test_context_git_fields_empty_without_workspace(self):
        recorder = RecordingGenerator()
        RunService(self.db, generator=recorder).start(
            "p", "mock", max_loops=2, require_approval=True, workspace=None
        )
        context = recorder.contexts[0]
        self.assertEqual(context.changed_files, [])
        self.assertEqual(context.git_diff_stat, "")


if __name__ == "__main__":
    unittest.main()
