"""Tests for the system API: stale-state status, reconciliation, and worker heartbeats.

Uses the FastAPI TestClient with a temp DB override. A RUNNING run is backdated so it is
genuinely stale under the real ``now`` the endpoints use (the reconcile core takes an explicit
``now`` for determinism, but the HTTP layer does not expose it). Standard library + fastapi.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_AUTH_ENV = ("AUTOPROMPT_AUTH_ENABLED", "AUTOPROMPT_API_TOKEN", "AUTOPROMPT_ALLOW_UNAUTHENTICATED_HEALTH")

try:
    from fastapi.testclient import TestClient

    from autoprompt_runner import storage
    from autoprompt_runner.api.app import app
    from autoprompt_runner.api.dependencies import get_db_path
    from autoprompt_runner.state import RunStatus

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False

_OLD_TS = "2000-01-01T00:00:00+00:00"  # far in the past -> always beyond timeout + grace


def _clear_auth_env():
    for name in _AUTH_ENV:
        os.environ.pop(name, None)


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class SystemApiTests(unittest.TestCase):
    def setUp(self):
        _clear_auth_env()
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        # A RUNNING run, backdated so it is stale under the real present.
        self.run_id = storage.create_run(
            self.db, root_prompt="do", provider="mock", max_loops=1, require_approval=False, timeout_seconds=60
        )
        storage.update_run_status(self.db, self.run_id, RunStatus.RUNNING.value)
        self._backdate_run(self.run_id)
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        _clear_auth_env()
        self._tmp.cleanup()

    def _backdate_run(self, run_id):
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE runs SET created_at = ? WHERE id = ?", (_OLD_TS, run_id))
            conn.commit()
        finally:
            conn.close()

    def _enable_auth(self, token="secret-token"):
        os.environ["AUTOPROMPT_AUTH_ENABLED"] = "true"
        os.environ["AUTOPROMPT_API_TOKEN"] = token

    def test_status_ok(self):
        resp = self.client.get("/system/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in (
            "active_workers", "stale_workers", "queued_jobs", "running_jobs",
            "active_locks", "stale_locks", "stale_runs", "generated_at",
        ):
            self.assertIn(key, body)
        self.assertEqual(body["stale_runs"], 1)  # the backdated RUNNING run

    def test_reconcile_dry_run_reports_but_does_not_modify(self):
        resp = self.client.post("/system/reconcile", json={"dry_run": True})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["stale_runs"], 1)
        # Nothing was changed.
        self.assertEqual(storage.get_run(self.db, self.run_id).status, RunStatus.RUNNING.value)
        self.assertEqual(storage.list_artifacts_for_run(self.db, self.run_id), [])

    def test_reconcile_apply_marks_run_failed(self):
        resp = self.client.post("/system/reconcile", json={"dry_run": False})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["dry_run"])
        self.assertEqual(body["stale_runs"], 1)
        self.assertGreaterEqual(len(body["actions"]), 1)
        self.assertEqual(storage.get_run(self.db, self.run_id).status, RunStatus.FAILED.value)
        types = [a.type for a in storage.list_artifacts_for_run(self.db, self.run_id)]
        self.assertIn("stale_run_detected", types)

    def test_reconcile_default_body_applies(self):
        # No JSON body -> default ReconcileRequest(dry_run=False) -> applies.
        resp = self.client.post("/system/reconcile")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["dry_run"])
        self.assertEqual(storage.get_run(self.db, self.run_id).status, RunStatus.FAILED.value)

    def test_workers_lists_heartbeat(self):
        storage.create_worker_heartbeat(self.db, "worker-test")
        resp = self.client.get("/system/workers")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["worker_id"], "worker-test")
        self.assertEqual(body[0]["status"], storage.WORKER_ACTIVE)

    def test_auth_required_when_enabled(self):
        self._enable_auth()
        self.assertEqual(self.client.get("/system/status").status_code, 401)
        self.assertEqual(self.client.post("/system/reconcile", json={"dry_run": True}).status_code, 401)
        self.assertEqual(self.client.get("/system/workers").status_code, 401)

    def test_auth_allows_valid_token(self):
        self._enable_auth(token="secret-token")
        headers = {"Authorization": "Bearer secret-token"}
        self.assertEqual(self.client.get("/system/status", headers=headers).status_code, 200)
        self.assertEqual(
            self.client.post("/system/reconcile", json={"dry_run": True}, headers=headers).status_code, 200
        )


if __name__ == "__main__":
    unittest.main()
