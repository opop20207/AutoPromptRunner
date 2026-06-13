"""Tests for the local background worker (standard library only).

Runnable via:
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

from autoprompt_runner import queue, storage  # noqa: E402
from autoprompt_runner.services import RunService  # noqa: E402
from autoprompt_runner.state import RunStatus  # noqa: E402
from autoprompt_runner.worker import LocalWorker  # noqa: E402


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.service = RunService(self.db)
        self.worker = LocalWorker(self.db, service=self.service, log=lambda _msg: None)

    def tearDown(self):
        self._tmp.cleanup()

    def _queue_run(self, prompt="p", max_loops=1, require_approval=False, workspace=None):
        run_id = self.service.create_run_only(
            prompt, "mock", max_loops, require_approval=require_approval, workspace=workspace
        )
        queue.enqueue(self.db, run_id)
        return run_id

    def test_run_once_executes_one_job(self):
        run_id = self._queue_run()
        self.assertTrue(self.worker.run_once())
        self.assertEqual(storage.get_run(self.db, run_id).status, RunStatus.DONE.value)
        job = storage.get_job_by_run_id(self.db, run_id)
        self.assertEqual(job.status, storage.QUEUE_DONE)
        self.assertEqual(job.attempts, 1)

    def test_run_once_empty_queue_returns_false(self):
        self.assertFalse(self.worker.run_once())

    def test_run_forever_stop_after_drains_queue(self):
        self._queue_run()
        self._queue_run()
        executed = self.worker.run_forever(stop_after=5)
        self.assertEqual(executed, 2)  # two jobs, then the empty queue ends the loop
        done = [j for j in storage.list_queue(self.db) if j.status == storage.QUEUE_DONE]
        self.assertEqual(len(done), 2)

    def test_worker_handles_failed_job(self):
        # A blocked prompt makes execution raise (SafetyBlockedError) -> the job is FAILED.
        run_id = self._queue_run(prompt="please run rm -rf / now")
        self.assertTrue(self.worker.run_once())
        job = storage.get_job_by_run_id(self.db, run_id)
        self.assertEqual(job.status, storage.QUEUE_FAILED)
        self.assertTrue(job.last_error)
        self.assertEqual(storage.get_run(self.db, run_id).status, RunStatus.FAILED.value)

    def test_worker_serializes_same_workspace(self):
        ws = os.path.join(self._tmp.name, "ws")
        os.makedirs(ws)
        run1 = self._queue_run(workspace=ws)
        run2 = self._queue_run(workspace=ws)
        self.worker.run_forever(stop_after=5)  # one worker runs them one at a time
        self.assertEqual(storage.get_run(self.db, run1).status, RunStatus.DONE.value)
        self.assertEqual(storage.get_run(self.db, run2).status, RunStatus.DONE.value)

    def test_worker_skips_cancelled_job(self):
        run_id = self._queue_run()
        self.service.cancel_run(run_id)  # cancels the queue job and stops the run
        self.assertFalse(self.worker.run_once())  # no QUEUED job remains to claim
        self.assertEqual(storage.get_run(self.db, run_id).status, RunStatus.STOPPED.value)
        self.assertEqual(len(storage.get_steps_for_run(self.db, run_id)), 0)  # never executed


if __name__ == "__main__":
    unittest.main()
