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

from autoprompt_runner import locks, providers, queue, storage  # noqa: E402
from autoprompt_runner.approvals import ApprovalStatus  # noqa: E402
from autoprompt_runner.models import AgentResult, NextPrompt  # noqa: E402
from autoprompt_runner.runners import ClaudeCodeRunner  # noqa: E402
from autoprompt_runner.runners.base import AgentRunner  # noqa: E402
from autoprompt_runner.services import RunService, RunServiceError  # noqa: E402
from autoprompt_runner.services.run_service import SafetyBlockedError, WorkspaceLockedError  # noqa: E402
from autoprompt_runner.state import RunStatus  # noqa: E402


class FailingRunner(AgentRunner):
    """A runner that always reports a non-zero exit (for testing the FAILED path)."""

    @property
    def name(self) -> str:
        return "mock"

    def run(self, prompt: str, run_id=None) -> AgentResult:
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


class ProviderProfileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_uses_provider_profile_command(self):
        # A profile whose name differs from its type resolves to the right runner + command.
        storage.create_provider_profile(
            self.db, name="claude-fast", type="claude-code", command="claude",
            default_timeout_seconds=1200, default_args="--model x",
        )
        runner = RunService(self.db)._make_runner("claude-fast", workspace=None, timeout_seconds=None)
        self.assertIsInstance(runner, ClaudeCodeRunner)
        self.assertEqual(runner.command, "claude")
        self.assertEqual(runner.timeout_seconds, 1200)  # profile default applies
        self.assertEqual(runner._build_argv("hi"), ["claude", "--model", "x", "-p", "hi"])

    def test_rejects_disabled_provider(self):
        providers.seed_default_provider_profiles(self.db)
        mock = storage.get_provider_profile_by_name(self.db, "mock")
        storage.set_provider_enabled(self.db, mock.id, False)
        svc = RunService(self.db)
        with self.assertRaises(RunServiceError) as ctx:
            svc.create_and_execute_run("p", "mock", 1, require_approval=False)
        self.assertEqual(ctx.exception.kind, "invalid")

    def test_seeded_mock_profile_still_runs(self):
        # With profiles seeded, the built-in mock name resolves through the profile path.
        providers.seed_default_provider_profiles(self.db)
        report = RunService(self.db).start("p", "mock", max_loops=1, require_approval=False)
        self.assertEqual(report.run_status, RunStatus.DONE.value)

    def test_create_run_only_like_reuses_source_settings(self):
        # Failure recovery creates a linked run that reuses the source run's settings.
        svc = RunService(self.db)
        source_id = svc.create_run_only(
            "original prompt", "mock", max_loops=3, require_approval=False, timeout_seconds=600
        )
        source = storage.get_run(self.db, source_id)
        recovery_run_id = svc.create_run_only_like(source, "recovery prompt")
        recovery_run = storage.get_run(self.db, recovery_run_id)
        self.assertNotEqual(recovery_run_id, source_id)
        self.assertEqual(recovery_run.provider, "mock")
        self.assertEqual(recovery_run.max_loops, 3)
        self.assertEqual(recovery_run.timeout_seconds, 600)
        self.assertEqual(recovery_run.root_prompt, "recovery prompt")
        self.assertEqual(storage.get_run(self.db, source_id).root_prompt, "original prompt")  # source untouched


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


class SafetyHardeningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_blocks_dangerous_prompt_before_execution(self):
        with self.assertRaises(SafetyBlockedError):
            RunService(self.db).start("please run rm -rf / on the repo", "mock", max_loops=2, require_approval=False)
        run = storage.list_runs(self.db)[0]
        self.assertEqual(run.status, RunStatus.FAILED.value)
        self.assertEqual(len(storage.get_steps_for_run(self.db, run.id)), 0)  # runner never executed
        self.assertTrue(storage.list_artifacts_for_run(self.db, run.id, artifact_type="safety_blocker"))

    def test_max_loops_hard_limit_raises(self):
        from autoprompt_runner import config

        with self.assertRaises(ValueError):
            RunService(self.db).start("p", "mock", max_loops=config.MAX_LOOPS_HARD_LIMIT + 1, require_approval=False)

    def test_stores_warnings_and_forces_approval_on_risky_change(self):
        repo = os.path.join(self._tmp.name, "repo")
        os.makedirs(repo)
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=test", "init", "-q"],
            cwd=repo, capture_output=True, text=True,
        )
        with open(os.path.join(repo, ".env"), "w", encoding="utf-8") as handle:
            handle.write("X=1\n")  # secret-like untracked file -> risky change
        report = RunService(self.db).start("update config", "mock", max_loops=2, require_approval=False, workspace=repo)
        # Risky change forces an approval gate even though require_approval was False.
        self.assertEqual(report.run_status, RunStatus.WAITING_APPROVAL.value)
        warnings = storage.list_artifacts_for_run(self.db, report.run_id, artifact_type="safety_warning")
        self.assertTrue(warnings)
        self.assertTrue(any("secret" in (w.content or "") for w in warnings))


class WorkspaceLockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.ws = os.path.join(self._tmp.name, "ws")
        self.ws2 = os.path.join(self._tmp.name, "ws2")
        os.makedirs(self.ws)
        os.makedirs(self.ws2)

    def tearDown(self):
        self._tmp.cleanup()

    def test_blocks_same_workspace_concurrent_run(self):
        locks.acquire_lock(self.db, self.ws, run_id=999, timeout_seconds=60)  # another active run holds it
        with self.assertRaises(WorkspaceLockedError):
            RunService(self.db).start("p", "mock", max_loops=1, require_approval=False, workspace=self.ws)
        run = storage.list_runs(self.db)[0]
        self.assertEqual(run.status, RunStatus.FAILED.value)
        self.assertTrue(storage.list_artifacts_for_run(self.db, run.id, artifact_type=locks.LOCK_BLOCKER_ARTIFACT))

    def test_allows_different_workspace(self):
        locks.acquire_lock(self.db, self.ws, run_id=999, timeout_seconds=60)  # ws is locked
        report = RunService(self.db).start("p", "mock", max_loops=1, require_approval=False, workspace=self.ws2)
        self.assertEqual(report.run_status, RunStatus.DONE.value)  # a different workspace is unaffected

    def test_waiting_approval_releases_lock(self):
        report = RunService(self.db).start("p", "mock", max_loops=3, require_approval=True, workspace=self.ws)
        self.assertEqual(report.run_status, RunStatus.WAITING_APPROVAL.value)
        self.assertIsNone(locks.active_lock_for_workspace(self.db, self.ws))  # released during review

    def test_approve_next_reacquires_lock(self):
        svc = RunService(self.db)
        run_id = svc.start("p", "mock", max_loops=3, require_approval=True, workspace=self.ws).run_id
        locks.acquire_lock(self.db, self.ws, run_id=999, timeout_seconds=60)  # someone else grabs it
        with self.assertRaises(WorkspaceLockedError):
            svc.approve_and_continue(run_id)
        self.assertEqual(storage.get_run(self.db, run_id).status, RunStatus.WAITING_APPROVAL.value)
        self.assertIsNotNone(storage.get_pending_approval(self.db, run_id))  # approval left intact
        locks.release_lock(self.db, 999)
        svc.approve_and_continue(run_id)  # now the workspace is free
        self.assertEqual(len(storage.get_steps_for_run(self.db, run_id)), 2)
        self.assertIsNone(locks.active_lock_for_workspace(self.db, self.ws))

    def test_mock_run_without_workspace_needs_no_lock(self):
        RunService(self.db).start("p", "mock", max_loops=1, require_approval=False)
        self.assertEqual(len(storage.list_locks(self.db)), 0)


class QueueExecutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.svc = RunService(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_run_only_does_not_execute(self):
        run_id = self.svc.create_run_only("p", "mock", 1, require_approval=False)
        self.assertEqual(storage.get_run(self.db, run_id).status, RunStatus.CREATED.value)
        self.assertEqual(len(storage.get_steps_for_run(self.db, run_id)), 0)

    def test_execute_queued_run_runs_it(self):
        run_id = self.svc.create_run_only("p", "mock", 1, require_approval=False)
        report = self.svc.execute_queued_run(run_id)
        self.assertEqual(report.run_status, RunStatus.DONE.value)
        self.assertEqual(len(storage.get_steps_for_run(self.db, run_id)), 1)

    def test_create_and_execute_run_matches_start(self):
        report = self.svc.create_and_execute_run("p", "mock", 1, require_approval=False)
        self.assertEqual(report.run_status, RunStatus.DONE.value)

    def test_execute_terminal_run_rejected(self):
        run_id = self.svc.create_run_only("p", "mock", 1, require_approval=False)
        self.svc.execute_queued_run(run_id)  # -> DONE
        with self.assertRaises(RunServiceError):
            self.svc.execute_queued_run(run_id)  # already terminal

    def test_queued_execution_still_acquires_lock(self):
        ws = os.path.join(self._tmp.name, "ws")
        os.makedirs(ws)
        locks.acquire_lock(self.db, ws, run_id=999, timeout_seconds=60)  # workspace held by another run
        run_id = self.svc.create_run_only("p", "mock", 1, require_approval=False, workspace=ws)
        with self.assertRaises(WorkspaceLockedError):
            self.svc.execute_queued_run(run_id)
        self.assertEqual(storage.get_run(self.db, run_id).status, RunStatus.FAILED.value)


class CancelRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.svc = RunService(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_cancel_queued_run(self):
        run_id = self.svc.create_run_only("p", "mock", 1, require_approval=False)
        queue.enqueue(self.db, run_id)
        result = self.svc.cancel_run(run_id, reason="user stop")
        self.assertTrue(result.cancelled)
        self.assertEqual(storage.get_run(self.db, run_id).status, RunStatus.STOPPED.value)
        self.assertEqual(storage.get_job_by_run_id(self.db, run_id).status, storage.QUEUE_CANCELLED)
        self.assertEqual(storage.get_cancellation_for_run(self.db, run_id).status, storage.CANCELLATION_COMPLETED)

    def test_cancel_creates_artifact(self):
        run_id = self.svc.create_run_only("p", "mock", 1, require_approval=False)
        queue.enqueue(self.db, run_id)
        self.svc.cancel_run(run_id, reason="stop")
        self.assertTrue(storage.list_artifacts_for_run(self.db, run_id, artifact_type="cancellation"))

    def test_cancel_waiting_approval_run(self):
        run_id = self.svc.create_and_execute_run("p", "mock", 3, require_approval=True).run_id
        self.assertEqual(storage.get_run(self.db, run_id).status, RunStatus.WAITING_APPROVAL.value)
        self.svc.cancel_run(run_id)
        self.assertEqual(storage.get_run(self.db, run_id).status, RunStatus.STOPPED.value)
        self.assertIsNone(storage.get_pending_approval(self.db, run_id))  # pending approval cleared

    def test_cancel_terminal_run_clean_error(self):
        run_id = self.svc.create_and_execute_run("p", "mock", 1, require_approval=False).run_id  # -> DONE
        with self.assertRaises(RunServiceError) as ctx:
            self.svc.cancel_run(run_id)
        self.assertEqual(ctx.exception.kind, "terminal")

    def test_cancel_missing_run_not_found(self):
        with self.assertRaises(RunServiceError) as ctx:
            self.svc.cancel_run(9999)
        self.assertEqual(ctx.exception.kind, "not_found")

    def test_cancel_releases_lock(self):
        ws = os.path.join(self._tmp.name, "ws")
        os.makedirs(ws)
        run_id = self.svc.create_run_only("p", "mock", 1, require_approval=False, workspace=ws)
        locks.acquire_lock(self.db, ws, run_id, timeout_seconds=60)
        storage.update_run_status(self.db, run_id, RunStatus.RUNNING.value)  # simulate a running, locked run
        self.svc.cancel_run(run_id)
        self.assertEqual(storage.get_run(self.db, run_id).status, RunStatus.STOPPED.value)
        self.assertIsNone(locks.active_lock_for_workspace(self.db, ws))  # lock released


if __name__ == "__main__":
    unittest.main()
