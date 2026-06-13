"""Tests for workspace execution locks (storage + locks orchestration; stdlib only).

Runnable via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import locks, storage  # noqa: E402


def _future_iso(seconds: int = 3600) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _past_iso(seconds: int = 3600) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LockStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.ws = locks.normalize_workspace(os.path.join(self._tmp.name, "ws"))

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_active_lock(self):
        lid = storage.create_run_lock(self.db, workspace_path=self.ws, run_id=1, expires_at=_future_iso())
        self.assertIsInstance(lid, int)
        active = storage.get_active_lock_for_workspace(self.db, self.ws)
        self.assertIsNotNone(active)
        self.assertEqual(active.run_id, 1)
        self.assertEqual(active.status, storage.LOCK_ACTIVE)

    def test_release_lock(self):
        storage.create_run_lock(self.db, workspace_path=self.ws, run_id=1, expires_at=_future_iso())
        self.assertEqual(storage.release_run_lock(self.db, 1), 1)
        self.assertIsNone(storage.get_active_lock_for_workspace(self.db, self.ws))
        self.assertEqual(storage.get_lock_for_run(self.db, 1).status, storage.LOCK_RELEASED)

    def test_expire_old_lock(self):
        storage.create_run_lock(self.db, workspace_path=self.ws, run_id=1, expires_at=_past_iso())
        self.assertEqual(storage.expire_old_locks(self.db, _now_iso()), 1)
        self.assertIsNone(storage.get_active_lock_for_workspace(self.db, self.ws))
        self.assertEqual(storage.get_lock_for_run(self.db, 1).status, storage.LOCK_EXPIRED)

    def test_expire_keeps_future_lock(self):
        storage.create_run_lock(self.db, workspace_path=self.ws, run_id=1, expires_at=_future_iso())
        self.assertEqual(storage.expire_old_locks(self.db, _now_iso()), 0)
        self.assertIsNotNone(storage.get_active_lock_for_workspace(self.db, self.ws))

    def test_list_locks(self):
        storage.create_run_lock(self.db, workspace_path=self.ws, run_id=1, expires_at=_future_iso())
        self.assertGreaterEqual(len(storage.list_locks(self.db)), 1)


class LockAcquireTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.ws = os.path.join(self._tmp.name, "ws")

    def tearDown(self):
        self._tmp.cleanup()

    def test_acquire_creates_active_lock(self):
        lock = locks.acquire_lock(self.db, self.ws, run_id=1, timeout_seconds=60)
        self.assertIsNotNone(lock)
        self.assertEqual(lock.status, storage.LOCK_ACTIVE)
        self.assertIsNotNone(lock.expires_at)

    def test_no_workspace_means_no_lock(self):
        self.assertIsNone(locks.acquire_lock(self.db, None, run_id=1))
        self.assertIsNone(locks.acquire_lock(self.db, "", run_id=1))
        self.assertEqual(len(storage.list_locks(self.db)), 0)

    def test_prevent_second_active_lock_for_same_workspace(self):
        locks.acquire_lock(self.db, self.ws, run_id=1, timeout_seconds=60)
        with self.assertRaises(locks.LockConflictError):
            locks.acquire_lock(self.db, self.ws, run_id=2, timeout_seconds=60)

    def test_same_run_reacquire_is_idempotent(self):
        first = locks.acquire_lock(self.db, self.ws, run_id=1, timeout_seconds=60)
        again = locks.acquire_lock(self.db, self.ws, run_id=1, timeout_seconds=60)
        self.assertEqual(first.id, again.id)
        active = [lk for lk in storage.list_locks(self.db) if lk.status == storage.LOCK_ACTIVE]
        self.assertEqual(len(active), 1)

    def test_release_then_another_run_can_acquire(self):
        locks.acquire_lock(self.db, self.ws, run_id=1, timeout_seconds=60)
        locks.release_lock(self.db, 1)
        lock = locks.acquire_lock(self.db, self.ws, run_id=2, timeout_seconds=60)
        self.assertEqual(lock.run_id, 2)

    def test_expired_lock_does_not_block_acquire(self):
        storage.create_run_lock(
            self.db, workspace_path=locks.normalize_workspace(self.ws), run_id=9, expires_at=_past_iso()
        )
        lock = locks.acquire_lock(self.db, self.ws, run_id=1, timeout_seconds=60)  # expires the stale lock first
        self.assertEqual(lock.run_id, 1)

    def test_normalized_path_comparison(self):
        locks.acquire_lock(self.db, self.ws, run_id=1, timeout_seconds=60)
        alt = self.ws + os.sep  # trailing separator -> same normalized key
        self.assertEqual(locks.normalize_workspace(alt), locks.normalize_workspace(self.ws))
        with self.assertRaises(locks.LockConflictError):
            locks.acquire_lock(self.db, alt, run_id=2, timeout_seconds=60)
        self.assertIsNotNone(locks.active_lock_for_workspace(self.db, alt))


if __name__ == "__main__":
    unittest.main()
