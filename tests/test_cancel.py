"""Tests for the process registry and run-cancellation storage (standard library only).

Runnable via:
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

from autoprompt_runner import cancel, processes, storage  # noqa: E402


class _FakeProc:
    """A fake Popen for the registry tests (no real subprocess is spawned)."""

    def __init__(self, alive=True, stubborn=False):
        self._alive = alive
        self._stubborn = stubborn  # require a kill (wait times out once) before exiting
        self._waits = 0
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        if not self._stubborn:
            self._alive = False

    def kill(self):
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        self._waits += 1
        if self._stubborn and self._waits == 1:
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
        return 0


class ProcessRegistryTests(unittest.TestCase):
    def tearDown(self):
        for run_id in (1, 2, 3):
            processes.unregister_process(run_id)
            processes.clear_terminated(run_id)

    def test_register_get_unregister(self):
        proc = _FakeProc()
        processes.register_process(1, proc)
        self.assertIs(processes.get_process(1), proc)
        processes.unregister_process(1)
        self.assertIsNone(processes.get_process(1))

    def test_terminate_missing_process_is_safe(self):
        self.assertFalse(processes.terminate_process(987654))

    def test_terminate_registered_process(self):
        proc = _FakeProc(alive=True)
        processes.register_process(2, proc)
        self.assertTrue(processes.terminate_process(2, grace_seconds=1))
        self.assertTrue(proc.terminated)
        self.assertTrue(processes.was_terminated(2))

    def test_terminate_escalates_to_kill(self):
        proc = _FakeProc(alive=True, stubborn=True)
        processes.register_process(3, proc)
        self.assertTrue(processes.terminate_process(3, grace_seconds=1))
        self.assertTrue(proc.terminated)
        self.assertTrue(proc.killed)  # wait timed out -> forced kill

    def test_terminate_already_exited_returns_false(self):
        proc = _FakeProc(alive=False)
        processes.register_process(2, proc)
        self.assertFalse(processes.terminate_process(2))


class CancellationStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.run_id = storage.create_run(
            self.db, root_prompt="p", provider="mock", max_loops=1, require_approval=False
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_request_cancellation_record(self):
        cancellation_id = storage.request_run_cancellation(self.db, self.run_id, reason="stop it")
        self.assertIsInstance(cancellation_id, int)
        record = storage.get_cancellation_for_run(self.db, self.run_id)
        self.assertEqual(record.status, storage.CANCELLATION_REQUESTED)
        self.assertEqual(record.reason, "stop it")
        self.assertIsNotNone(record.requested_at)

    def test_complete_cancellation(self):
        cancellation_id = storage.request_run_cancellation(self.db, self.run_id)
        storage.complete_run_cancellation(self.db, cancellation_id)
        self.assertEqual(
            storage.get_cancellation_for_run(self.db, self.run_id).status, storage.CANCELLATION_COMPLETED
        )

    def test_fail_cancellation_records_error(self):
        cancellation_id = storage.request_run_cancellation(self.db, self.run_id)
        storage.fail_run_cancellation(self.db, cancellation_id, "boom")
        record = storage.get_cancellation_for_run(self.db, self.run_id)
        self.assertEqual(record.status, storage.CANCELLATION_FAILED)
        self.assertEqual(record.error, "boom")

    def test_list_cancellations(self):
        storage.request_run_cancellation(self.db, self.run_id)
        self.assertGreaterEqual(len(storage.list_cancellations(self.db)), 1)

    def test_status_constants_are_shared(self):
        self.assertEqual(cancel.CANCELLATION_REQUESTED, storage.CANCELLATION_REQUESTED)
        self.assertEqual(cancel.CANCELLATION_COMPLETED, storage.CANCELLATION_COMPLETED)


if __name__ == "__main__":
    unittest.main()
