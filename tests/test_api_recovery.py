"""Tests for the failure recovery API endpoints (FastAPI TestClient + temp DB override)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from fastapi.testclient import TestClient

    from autoprompt_runner import storage
    from autoprompt_runner.api.app import app
    from autoprompt_runner.api.dependencies import get_db_path
    from autoprompt_runner.state import RunStatus

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class RecoveryApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.failed = storage.create_run(
            self.db, root_prompt="Fix it", provider="mock", max_loops=1, require_approval=False
        )
        storage.create_step(self.db, self.failed, 0, "run", "FAILED", stderr="boom traceback", exit_code=1)
        storage.update_run_status(self.db, self.failed, RunStatus.FAILED.value)
        self.done = storage.create_run(self.db, root_prompt="ok", provider="mock", max_loops=1, require_approval=False)
        storage.update_run_status(self.db, self.done, RunStatus.RUNNING.value)
        storage.update_run_status(self.db, self.done, RunStatus.DONE.value)
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self._tmp.cleanup()

    def test_propose(self):
        resp = self.client.post(f"/recovery/runs/{self.failed}/propose", json={"reason": "x"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "PROPOSED")
        self.assertEqual(body["source_run_id"], self.failed)

    def test_propose_non_failed_returns_400(self):
        self.assertEqual(self.client.post(f"/recovery/runs/{self.done}/propose").status_code, 400)

    def test_propose_missing_run_returns_404(self):
        self.assertEqual(self.client.post("/recovery/runs/9999/propose").status_code, 404)

    def test_list_for_run(self):
        self.client.post(f"/recovery/runs/{self.failed}/propose")
        resp = self.client.get(f"/recovery/runs/{self.failed}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["recoveries"]), 1)

    def test_list_missing_run_returns_404(self):
        self.assertEqual(self.client.get("/recovery/runs/9999").status_code, 404)

    def test_approve_and_execute(self):
        proposed = self.client.post(f"/recovery/runs/{self.failed}/propose").json()
        rid = proposed["id"]
        self.assertEqual(self.client.post(f"/recovery/{rid}/approve").json()["status"], "APPROVED")
        executed = self.client.post(f"/recovery/{rid}/execute", json={"queued": False}).json()
        self.assertIsNotNone(executed["recovery_run_id"])
        self.assertEqual(executed["status"], "EXECUTED")
        # The linked recovery run exists and differs from the source.
        self.assertNotEqual(executed["recovery_run_id"], self.failed)
        self.assertEqual(self.client.get(f"/runs/{executed['recovery_run_id']}").status_code, 200)

    def test_reject_then_execute_returns_409(self):
        rid = self.client.post(f"/recovery/runs/{self.failed}/propose").json()["id"]
        self.assertEqual(self.client.post(f"/recovery/{rid}/reject", json={"reason": "no"}).json()["status"], "REJECTED")
        self.assertEqual(self.client.post(f"/recovery/{rid}/execute").status_code, 409)

    def test_missing_recovery_returns_404(self):
        self.assertEqual(self.client.post("/recovery/9999/approve").status_code, 404)

    def test_list_all(self):
        self.client.post(f"/recovery/runs/{self.failed}/propose")
        self.assertGreaterEqual(len(self.client.get("/recovery").json()["recoveries"]), 1)


if __name__ == "__main__":
    unittest.main()
