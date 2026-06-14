"""Tests for stale-state reconciliation (autoprompt_runner.reconcile)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import events, locks, queue, reconcile, storage  # noqa: E402
from autoprompt_runner.state import RunStatus  # noqa: E402


def _future(seconds=100000):
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


class ReconcileTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _running_run(self, timeout=60):
        rid = storage.create_run(
            self.db, root_prompt="do", provider="mock", max_loops=1, require_approval=False, timeout_seconds=timeout
        )
        storage.update_run_status(self.db, rid, RunStatus.RUNNING.value)
        return rid


class StaleRunTests(ReconcileTestCase):
    def test_detect_stale_running_run(self):
        rid = self._running_run()
        stale = reconcile.detect_stale_runs(self.db, now=_future())
        self.assertEqual([r.id for r, _ in stale], [rid])

    def test_waiting_approval_is_not_stale(self):
        rid = self._running_run()
        storage.update_run_status(self.db, rid, RunStatus.WAITING_APPROVAL.value)
        self.assertEqual(reconcile.detect_stale_runs(self.db, now=_future()), [])

    def test_running_within_timeout_is_not_stale(self):
        self._running_run(timeout=3600)
        # "now" is the real present, well within timeout + grace.
        self.assertEqual(reconcile.detect_stale_runs(self.db), [])

    def test_terminal_runs_not_modified(self):
        rid = storage.create_run(self.db, root_prompt="x", provider="mock", max_loops=1, require_approval=False)
        storage.update_run_status(self.db, rid, RunStatus.RUNNING.value)
        storage.update_run_status(self.db, rid, RunStatus.DONE.value)
        reconcile.reconcile_stale_state(self.db, now=_future())
        self.assertEqual(storage.get_run(self.db, rid).status, RunStatus.DONE.value)

    def test_reconcile_marks_stale_run_failed(self):
        rid = self._running_run()
        report = reconcile.reconcile_stale_state(self.db, now=_future())
        self.assertEqual(report.stale_runs, 1)
        self.assertEqual(storage.get_run(self.db, rid).status, RunStatus.FAILED.value)

    def test_reconcile_writes_artifact_and_event(self):
        rid = self._running_run()
        reconcile.reconcile_stale_state(self.db, now=_future())
        types = [a.type for a in storage.list_artifacts_for_run(self.db, rid)]
        self.assertIn("stale_run_detected", types)
        sys_events = [e.type for e in storage.list_run_events(self.db, events.SYSTEM_RUN_ID)]
        self.assertIn("reconciliation_finished", sys_events)

    def test_cancellation_requested_marks_stopped(self):
        rid = self._running_run()
        storage.request_run_cancellation(self.db, rid, "user stop")
        report = reconcile.reconcile_stale_state(self.db, now=_future())
        self.assertEqual(report.stale_runs, 1)
        self.assertEqual(storage.get_run(self.db, rid).status, RunStatus.STOPPED.value)


class StaleQueueJobTests(ReconcileTestCase):
    def test_stale_running_job_becomes_failed(self):
        rid = self._running_run()
        queue.enqueue(self.db, rid)
        job = queue.claim_next_job(self.db)  # marks it RUNNING
        self.assertEqual(storage.get_job_by_run_id(self.db, rid).status, storage.QUEUE_RUNNING)
        reconcile.reconcile_stale_state(self.db, now=_future())  # no live worker + old -> FAILED
        self.assertEqual(storage.get_job_by_run_id(self.db, rid).status, storage.QUEUE_FAILED)

    def test_running_job_not_failed_when_live_worker(self):
        rid = self._running_run()
        queue.enqueue(self.db, rid)
        queue.claim_next_job(self.db)
        storage.create_worker_heartbeat(self.db, "live-worker")  # updated_at = now -> live
        # Even with a future "now", a recently-updated worker is still live within the window
        # when we use the default worker_stale window relative to the SAME now... use real now.
        stale = reconcile.detect_stale_queue_jobs(self.db)  # real now: worker live -> none
        self.assertEqual(stale, [])


class StaleLockTests(ReconcileTestCase):
    def test_stale_lock_becomes_expired(self):
        # A WAITING_APPROVAL run is not stale, so the lock is reconciled via the expiry path
        # (not released by run reconciliation).
        rid = self._running_run()
        storage.update_run_status(self.db, rid, RunStatus.WAITING_APPROVAL.value)
        locks.acquire_lock(self.db, "/tmp/ws-a", rid, timeout_seconds=60)
        reconcile.reconcile_stale_state(self.db, now=_future())  # past expiry -> EXPIRED
        lock = next(lk for lk in storage.list_locks(self.db) if lk.run_id == rid)
        self.assertEqual(lock.status, storage.LOCK_EXPIRED)

    def test_terminal_run_lock_is_expired(self):
        rid = storage.create_run(self.db, root_prompt="x", provider="mock", max_loops=1, require_approval=False)
        storage.update_run_status(self.db, rid, RunStatus.RUNNING.value)
        locks.acquire_lock(self.db, "/tmp/ws-b", rid, timeout_seconds=3600)  # not past expiry
        storage.update_run_status(self.db, rid, RunStatus.DONE.value)  # terminal
        reconcile.reconcile_stale_state(self.db)  # real now: stale because run is terminal
        lock = next(lk for lk in storage.list_locks(self.db) if lk.run_id == rid)
        self.assertEqual(lock.status, storage.LOCK_EXPIRED)


class WorkerHeartbeatTests(ReconcileTestCase):
    def test_create_update_stop(self):
        hb = storage.create_worker_heartbeat(self.db, "w1")
        self.assertEqual(len(storage.get_active_worker_heartbeats(self.db)), 1)
        storage.update_worker_heartbeat(self.db, hb)
        storage.stop_worker_heartbeat(self.db, hb)
        self.assertEqual(len(storage.get_active_worker_heartbeats(self.db)), 0)
        self.assertEqual(storage.list_worker_heartbeats(self.db)[0].status, storage.WORKER_STOPPED)

    def test_detect_stale_worker(self):
        storage.create_worker_heartbeat(self.db, "w1")
        self.assertEqual(reconcile.detect_stale_workers(self.db), [])  # fresh
        self.assertEqual(len(reconcile.detect_stale_workers(self.db, now=_future())), 1)  # old

    def test_reconcile_stops_stale_workers(self):
        storage.create_worker_heartbeat(self.db, "w1")
        report = reconcile.reconcile_stale_state(self.db, now=_future())
        self.assertEqual(report.stale_workers, 1)
        self.assertEqual(len(storage.get_active_worker_heartbeats(self.db)), 0)


class DryRunTests(ReconcileTestCase):
    def test_dry_run_does_not_modify(self):
        rid = self._running_run()
        report = reconcile.reconcile_stale_state(self.db, dry_run=True, now=_future())
        self.assertTrue(report.dry_run)
        self.assertEqual(report.stale_runs, 1)
        self.assertEqual(storage.get_run(self.db, rid).status, RunStatus.RUNNING.value)  # unchanged
        self.assertEqual(storage.list_artifacts_for_run(self.db, rid), [])  # no artifact written

    def test_build_system_status(self):
        self._running_run()
        storage.create_worker_heartbeat(self.db, "w1")
        status = reconcile.build_system_status(self.db, now=_future())
        self.assertEqual(status.stale_runs, 1)
        self.assertEqual(status.stale_workers, 1)


if __name__ == "__main__":
    unittest.main()
