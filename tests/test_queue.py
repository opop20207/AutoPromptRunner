"""Tests for the local run queue (storage + queue orchestration; standard library only).

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


class _QueueTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.run1 = storage.create_run(self.db, root_prompt="a", provider="mock", max_loops=1, require_approval=False)
        self.run2 = storage.create_run(self.db, root_prompt="b", provider="mock", max_loops=1, require_approval=False)

    def tearDown(self):
        self._tmp.cleanup()


class QueueStorageTests(_QueueTestCase):
    def test_enqueue_run(self):
        job_id = storage.enqueue_run(self.db, self.run1)
        self.assertIsInstance(job_id, int)
        job = storage.get_job_by_run_id(self.db, self.run1)
        self.assertEqual(job.status, storage.QUEUE_QUEUED)
        self.assertEqual(job.run_id, self.run1)
        self.assertEqual(job.priority, 100)
        self.assertEqual(job.attempts, 0)

    def test_prevent_duplicate_active_job(self):
        storage.enqueue_run(self.db, self.run1)
        with self.assertRaises(ValueError):
            storage.enqueue_run(self.db, self.run1)

    def test_get_next_priority_order(self):
        storage.enqueue_run(self.db, self.run2, priority=100)
        storage.enqueue_run(self.db, self.run1, priority=10)  # lower number -> runs first
        self.assertEqual(storage.get_next_queued_job(self.db).run_id, self.run1)

    def test_get_next_fifo_when_equal_priority(self):
        storage.enqueue_run(self.db, self.run2, priority=100)  # enqueued first
        storage.enqueue_run(self.db, self.run1, priority=100)
        self.assertEqual(storage.get_next_queued_job(self.db).run_id, self.run2)  # oldest first

    def test_mark_running_then_done(self):
        job_id = storage.enqueue_run(self.db, self.run1)
        storage.mark_job_running(self.db, job_id)
        job = storage.get_job_by_run_id(self.db, self.run1)
        self.assertEqual(job.status, storage.QUEUE_RUNNING)
        self.assertEqual(job.attempts, 1)
        self.assertIsNotNone(job.started_at)
        storage.mark_job_done(self.db, job_id)
        self.assertEqual(storage.get_job_by_run_id(self.db, self.run1).status, storage.QUEUE_DONE)

    def test_mark_failed_records_error(self):
        job_id = storage.enqueue_run(self.db, self.run1)
        storage.mark_job_failed(self.db, job_id, "boom")
        job = storage.get_job_by_run_id(self.db, self.run1)
        self.assertEqual(job.status, storage.QUEUE_FAILED)
        self.assertEqual(job.last_error, "boom")

    def test_cancel_queued_job(self):
        storage.enqueue_run(self.db, self.run1)
        self.assertEqual(storage.cancel_job(self.db, self.run1), 1)
        self.assertEqual(storage.get_job_by_run_id(self.db, self.run1).status, storage.QUEUE_CANCELLED)

    def test_list_queue(self):
        storage.enqueue_run(self.db, self.run1)
        storage.enqueue_run(self.db, self.run2)
        self.assertEqual(len(storage.list_queue(self.db)), 2)


class QueuePolicyTests(_QueueTestCase):
    def test_claim_next_marks_running(self):
        queue.enqueue(self.db, self.run1)
        job = queue.claim_next_job(self.db)
        self.assertIsNotNone(job)
        self.assertEqual(job.run_id, self.run1)
        self.assertEqual(storage.get_job_by_run_id(self.db, self.run1).status, storage.QUEUE_RUNNING)
        self.assertIsNone(queue.claim_next_job(self.db))  # nothing left queued

    def test_cancel_policy_running(self):
        queue.enqueue(self.db, self.run1)
        queue.claim_next_job(self.db)  # now RUNNING
        self.assertEqual(queue.cancel(self.db, self.run1), queue.CANCEL_RUNNING)

    def test_cancel_policy_not_found(self):
        self.assertEqual(queue.cancel(self.db, 9999), queue.CANCEL_NOT_FOUND)

    def test_cancel_policy_queued(self):
        queue.enqueue(self.db, self.run1)
        self.assertEqual(queue.cancel(self.db, self.run1), queue.CANCEL_CANCELLED)
        self.assertEqual(storage.get_job_by_run_id(self.db, self.run1).status, storage.QUEUE_CANCELLED)


if __name__ == "__main__":
    unittest.main()
