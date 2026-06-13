"""Tests for the run + artifact API endpoints (FastAPI TestClient + temp DB override)."""

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

    from autoprompt_runner import locks, storage
    from autoprompt_runner.api.app import app
    from autoprompt_runner.api.dependencies import get_db_path

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class RunApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self._tmp.cleanup()

    def _run(self, prompt="Continue next task", max_loops=3, **extra):
        body = {"prompt": prompt, "max_loops": max_loops}
        body.update(extra)
        return self.client.post("/runs", json=body)

    def test_create_run_mock_waits_for_approval(self):
        resp = self._run(max_loops=3)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "WAITING_APPROVAL")
        self.assertIsNotNone(body["approval_id"])

    def test_create_run_invalid_provider_returns_400(self):
        resp = self.client.post("/runs", json={"prompt": "x", "provider": "nope"})
        self.assertEqual(resp.status_code, 400)

    def test_create_run_missing_project_returns_404(self):
        resp = self.client.post("/runs", json={"prompt": "x", "project": "missing"})
        self.assertEqual(resp.status_code, 404)

    def test_list_runs(self):
        self._run()
        resp = self.client.get("/runs")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.json()), 1)

    def test_get_run_detail(self):
        run_id = self._run(max_loops=3).json()["id"]
        resp = self.client.get(f"/runs/{run_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["id"], run_id)
        self.assertEqual(len(body["steps"]), 1)
        self.assertIsNotNone(body["pending_approval"])
        self.assertGreaterEqual(len(body["artifacts"]), 1)

    def test_get_run_missing_returns_404(self):
        self.assertEqual(self.client.get("/runs/999").status_code, 404)

    def test_approve_next_executes_step(self):
        run_id = self._run(max_loops=3).json()["id"]
        resp = self.client.post(f"/runs/{run_id}/approve-next")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.client.get(f"/runs/{run_id}").json()["steps"]), 2)

    def test_reject_next_stops_run(self):
        run_id = self._run(max_loops=3).json()["id"]
        resp = self.client.post(f"/runs/{run_id}/reject-next")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "STOPPED")

    def test_approve_next_terminal_returns_409(self):
        run_id = self._run(max_loops=1).json()["id"]  # ends DONE
        self.assertEqual(self.client.post(f"/runs/{run_id}/approve-next").status_code, 409)

    def test_reject_next_no_pending_returns_400(self):
        run_id = self._run(max_loops=1).json()["id"]  # DONE, no pending approval
        self.assertEqual(self.client.post(f"/runs/{run_id}/reject-next").status_code, 400)

    def test_run_artifacts_and_type_filter(self):
        run_id = self._run(max_loops=1).json()["id"]
        resp = self.client.get(f"/runs/{run_id}/artifacts")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("runner_stdout", [a["type"] for a in resp.json()])
        filtered = self.client.get(f"/runs/{run_id}/artifacts?type=runner_stdout").json()
        self.assertTrue(filtered)
        self.assertTrue(all(a["type"] == "runner_stdout" for a in filtered))

    def test_get_artifact_detail_and_missing(self):
        run_id = self._run(max_loops=1).json()["id"]
        artifact_id = self.client.get(f"/runs/{run_id}/artifacts").json()[0]["id"]
        resp = self.client.get(f"/artifacts/{artifact_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], artifact_id)
        self.assertEqual(self.client.get("/artifacts/99999").status_code, 404)

    def test_run_logs_for_existing_run(self):
        run_id = self._run(max_loops=1).json()["id"]  # ends DONE; mock runner artifacts captured
        resp = self.client.get(f"/runs/{run_id}/logs")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["run_id"], run_id)
        self.assertEqual(body["status"], "DONE")
        self.assertIn("generated_at", body)
        self.assertIsNotNone(body["latest_step_id"])
        self.assertIn("mock", body["stdout"].lower())
        self.assertIsNotNone(body["stdout_artifact_id"])

    def test_run_logs_missing_run_returns_404(self):
        self.assertEqual(self.client.get("/runs/9999/logs").status_code, 404)

    def test_create_run_blocked_prompt_returns_400(self):
        resp = self.client.post("/runs", json={"prompt": "then run rm -rf / on the repo", "max_loops": 1})
        self.assertEqual(resp.status_code, 400)

    def test_create_run_max_loops_above_hard_limit_returns_400(self):
        resp = self.client.post("/runs", json={"prompt": "p", "max_loops": 9999})
        self.assertEqual(resp.status_code, 400)

    def test_locks_list_endpoint(self):
        resp = self.client.get("/locks")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_create_run_locked_workspace_returns_409(self):
        ws = os.path.join(self._tmp.name, "wsA")
        os.makedirs(ws)
        locks.acquire_lock(self.db, ws, run_id=999, timeout_seconds=60)  # held by another run
        resp = self.client.post(
            "/runs", json={"prompt": "p", "workspace": ws, "provider": "mock", "max_loops": 1, "require_approval": False}
        )
        self.assertEqual(resp.status_code, 409)

    def test_release_lock_endpoint(self):
        ws = os.path.join(self._tmp.name, "wsB")
        os.makedirs(ws)
        locks.acquire_lock(self.db, ws, run_id=77, timeout_seconds=60)
        resp = self.client.post("/locks/77/release")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["released"], 1)
        self.assertIsNone(locks.active_lock_for_workspace(self.db, ws))

    def test_approve_next_locked_workspace_returns_409(self):
        ws = os.path.join(self._tmp.name, "wsC")
        os.makedirs(ws)
        run_id = self._run(workspace=ws, max_loops=3, require_approval=True).json()["id"]  # pauses at approval
        locks.acquire_lock(self.db, ws, run_id=999, timeout_seconds=60)  # another run grabs the workspace
        self.assertEqual(self.client.post(f"/runs/{run_id}/approve-next").status_code, 409)


if __name__ == "__main__":
    unittest.main()
