"""Tests for the SQLite storage layer and the run-status state machine.

Standard-library only (unittest + tempfile). Runnable via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

# Make the src-layout package importable without installing it.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import storage  # noqa: E402
from autoprompt_runner.state import RunStatus, validate_status_transition  # noqa: E402


class StorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_init_db_creates_file(self):
        path = storage.init_db(self.db)
        self.assertTrue(os.path.exists(self.db))
        self.assertEqual(os.path.abspath(path), os.path.abspath(self.db))

    def test_init_db_creates_missing_parent_dir(self):
        nested = os.path.join(self._tmp.name, "nested", "dir", "autoprompt.db")
        storage.init_db(nested)
        self.assertTrue(os.path.exists(nested))

    def test_create_and_get_run(self):
        storage.init_db(self.db)
        run_id = storage.create_run(
            self.db, root_prompt="hello world", provider="mock",
            max_loops=3, require_approval=True,
        )
        self.assertIsInstance(run_id, int)
        run = storage.get_run(self.db, run_id)
        self.assertIsNotNone(run)
        self.assertEqual(run.id, run_id)
        self.assertEqual(run.root_prompt, "hello world")
        self.assertEqual(run.provider, "mock")
        self.assertEqual(run.status, RunStatus.CREATED.value)
        self.assertEqual(run.max_loops, 3)
        self.assertTrue(run.require_approval)
        self.assertIsNone(run.finished_at)

    def test_get_run_missing_returns_none(self):
        storage.init_db(self.db)
        self.assertIsNone(storage.get_run(self.db, 999))

    def test_create_step_and_get_steps_for_run(self):
        storage.init_db(self.db)
        run_id = storage.create_run(
            self.db, root_prompt="p", provider="mock",
            max_loops=1, require_approval=False,
        )
        step_id = storage.create_step(
            self.db, run_id=run_id, loop_index=0, prompt="p",
            status=RunStatus.DONE.value, stdout="out", stderr="",
            exit_code=0, started_at="t0", finished_at="t1", next_prompt=None,
        )
        self.assertIsInstance(step_id, int)
        steps = storage.get_steps_for_run(self.db, run_id)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].run_id, run_id)
        self.assertEqual(steps[0].loop_index, 0)
        self.assertEqual(steps[0].prompt, "p")
        self.assertEqual(steps[0].exit_code, 0)
        self.assertEqual(steps[0].stdout, "out")
        self.assertEqual(steps[0].status, RunStatus.DONE.value)

    def test_list_runs_returns_created_runs_newest_first(self):
        storage.init_db(self.db)
        first = storage.create_run(self.db, root_prompt="a", provider="mock", max_loops=1, require_approval=True)
        second = storage.create_run(self.db, root_prompt="b", provider="mock", max_loops=1, require_approval=True)
        runs = storage.list_runs(self.db)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0].id, second)  # newest first
        self.assertEqual({r.id for r in runs}, {first, second})

    def test_update_run_status_sets_finished_at_on_terminal(self):
        storage.init_db(self.db)
        run_id = storage.create_run(self.db, root_prompt="p", provider="mock", max_loops=1, require_approval=True)
        storage.update_run_status(self.db, run_id, RunStatus.RUNNING.value)
        storage.update_run_status(self.db, run_id, RunStatus.DONE.value)
        run = storage.get_run(self.db, run_id)
        self.assertEqual(run.status, RunStatus.DONE.value)
        self.assertIsNotNone(run.finished_at)

    def test_update_run_status_rejects_invalid_transition(self):
        storage.init_db(self.db)
        run_id = storage.create_run(self.db, root_prompt="p", provider="mock", max_loops=1, require_approval=True)
        # CREATED -> DONE is not a legal transition.
        with self.assertRaises(ValueError):
            storage.update_run_status(self.db, run_id, RunStatus.DONE.value)


class StateTransitionTests(unittest.TestCase):
    def test_valid_transitions(self):
        self.assertEqual(
            validate_status_transition(RunStatus.CREATED, RunStatus.RUNNING), RunStatus.RUNNING
        )
        self.assertEqual(validate_status_transition("RUNNING", "DONE"), RunStatus.DONE)

    def test_invalid_transition_raises_value_error(self):
        with self.assertRaises(ValueError):
            validate_status_transition(RunStatus.DONE, RunStatus.RUNNING)
        with self.assertRaises(ValueError):
            validate_status_transition(RunStatus.CREATED, RunStatus.DONE)

    def test_unknown_status_raises_value_error(self):
        with self.assertRaises(ValueError):
            validate_status_transition("CREATED", "NOPE")


class RunLogsStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.run_id = storage.create_run(
            self.db, root_prompt="p", provider="mock", max_loops=2, require_approval=False
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_get_latest_step_for_run(self):
        storage.create_step(self.db, run_id=self.run_id, loop_index=0, prompt="a", status="DONE")
        second = storage.create_step(self.db, run_id=self.run_id, loop_index=1, prompt="b", status="DONE")
        latest = storage.get_latest_step_for_run(self.db, self.run_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.id, second)
        self.assertEqual(latest.loop_index, 1)

    def test_get_latest_step_none_when_no_steps(self):
        self.assertIsNone(storage.get_latest_step_for_run(self.db, self.run_id))

    def test_get_latest_artifact_by_type(self):
        step_id = storage.create_step(self.db, run_id=self.run_id, loop_index=0, prompt="a", status="DONE")
        storage.create_artifact(self.db, self.run_id, "runner_stdout", content="first", step_id=step_id)
        second = storage.create_artifact(self.db, self.run_id, "runner_stdout", content="second", step_id=step_id)
        latest = storage.get_latest_artifact_by_type(self.db, self.run_id, "runner_stdout")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.id, second)
        self.assertEqual(latest.content, "second")
        self.assertIsNone(storage.get_latest_artifact_by_type(self.db, self.run_id, "git_diff"))

    def test_get_run_logs(self):
        step_id = storage.create_step(
            self.db, run_id=self.run_id, loop_index=0, prompt="a", status="DONE",
            stdout="step out", stderr="step err",
        )
        storage.create_artifact(self.db, self.run_id, "runner_stdout", content="hello stdout", step_id=step_id)
        storage.create_artifact(self.db, self.run_id, "runner_stderr", content="hello stderr", step_id=step_id)
        logs = storage.get_run_logs(self.db, self.run_id)
        self.assertIsNotNone(logs)
        self.assertEqual(logs["run_id"], self.run_id)
        self.assertEqual(logs["status"], RunStatus.CREATED.value)
        self.assertEqual(logs["latest_step_id"], step_id)
        self.assertEqual(logs["stdout"], "hello stdout")
        self.assertEqual(logs["stderr"], "hello stderr")
        self.assertIsNotNone(logs["stdout_artifact_id"])
        self.assertIsNotNone(logs["stderr_artifact_id"])
        self.assertIn("generated_at", logs)

    def test_get_run_logs_missing_returns_none(self):
        self.assertIsNone(storage.get_run_logs(self.db, 9999))


if __name__ == "__main__":
    unittest.main()
