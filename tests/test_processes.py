"""Tests for the process registry and cross-platform cancellation (autoprompt_runner.processes).

Uses fake process objects (duck-typed ``Popen``) so no real subprocess is launched and the
terminate/kill escalation is exercised deterministically on any OS. Standard library only.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import processes  # noqa: E402


class FakeProcess:
    """A minimal stand-in for ``subprocess.Popen`` (poll / terminate / kill / wait)."""

    def __init__(self, alive=True, terminate_stops=True, raise_on_terminate=None):
        self._alive = alive
        self._terminate_stops = terminate_stops
        self._raise_on_terminate = raise_on_terminate
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        if self._raise_on_terminate is not None:
            raise self._raise_on_terminate
        if self._terminate_stops:
            self._alive = False

    def kill(self):
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        if self._alive:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return 0


class ProcessRegistryTests(unittest.TestCase):
    def setUp(self):
        self._ids = []

    def tearDown(self):
        for run_id in self._ids:
            processes.unregister_process(run_id)
            processes.clear_terminated(run_id)

    def _register(self, run_id, process):
        self._ids.append(run_id)
        processes.register_process(run_id, process)
        return process

    def test_register_get_unregister(self):
        proc = self._register(9001, FakeProcess())
        self.assertIs(processes.get_process(9001), proc)
        processes.unregister_process(9001)
        self.assertIsNone(processes.get_process(9001))

    def test_unregister_missing_is_safe(self):
        processes.unregister_process(9999)  # no error

    def test_terminate_missing_process_returns_false(self):
        self.assertFalse(processes.terminate_process(8888))

    def test_terminate_already_exited_returns_false(self):
        proc = self._register(9002, FakeProcess(alive=False))
        self.assertFalse(processes.terminate_process(9002))
        self.assertFalse(proc.terminated)
        self.assertFalse(processes.was_terminated(9002))

    def test_terminate_graceful_stop(self):
        proc = self._register(9003, FakeProcess(alive=True, terminate_stops=True))
        self.assertTrue(processes.terminate_process(9003, grace_seconds=1))
        self.assertTrue(proc.terminated)
        self.assertFalse(proc.killed)  # stopped gracefully; no force kill
        self.assertTrue(processes.was_terminated(9003))

    def test_terminate_escalates_to_kill(self):
        proc = self._register(9004, FakeProcess(alive=True, terminate_stops=False))
        self.assertTrue(processes.terminate_process(9004, grace_seconds=1))
        self.assertTrue(proc.terminated)
        self.assertTrue(proc.killed)  # still alive after terminate -> forced kill

    def test_terminate_handles_terminate_raising(self):
        # A process that exits between poll() and terminate() (terminate raises) is handled safely.
        proc = self._register(9005, FakeProcess(alive=True, raise_on_terminate=OSError("gone")))
        self.assertTrue(processes.terminate_process(9005, grace_seconds=1))
        self.assertTrue(proc.terminated)

    def test_was_terminated_and_clear(self):
        self._register(9006, FakeProcess(alive=True, terminate_stops=True))
        processes.terminate_process(9006, grace_seconds=1)
        self.assertTrue(processes.was_terminated(9006))
        processes.clear_terminated(9006)
        self.assertFalse(processes.was_terminated(9006))

    def test_clear_terminated_missing_is_safe(self):
        processes.clear_terminated(7777)  # no error


if __name__ == "__main__":
    unittest.main()
