"""End-to-end HTTP API flow for the v0.1 MVP via the FastAPI TestClient + MockRunner.

Walks health -> templates -> project (default) -> synchronous run -> approve/reject ->
queued run -> queue listing/cancel -> artifacts in one TestClient session. No real Claude
Code / Codex CLI and no outbound network are required (TestClient is in-process).

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

try:
    from fastapi.testclient import TestClient

    from autoprompt_runner import storage
    from autoprompt_runner.api.app import app
    from autoprompt_runner.api.dependencies import get_db_path

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover - only when the api extra is absent
    _HAVE_FASTAPI = False


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class E2EApiFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        self.ws = os.path.join(self._tmp.name, "workspace")
        os.makedirs(self.ws)
        storage.init_db(self.db)
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self._tmp.cleanup()

    def test_full_api_flow(self):
        # 1) health (with safe config metadata)
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        self.assertIn("config", health.json())

        # 2) seed templates
        self.assertEqual(self.client.post("/templates/seed").status_code, 200)

        # 3) create a project + 4) set it default
        proj = self.client.post(
            "/projects",
            json={"name": "P", "repo_path": self.ws, "default_provider": "mock",
                  "default_max_loops": 3, "require_approval": True, "timeout_seconds": 1800},
        )
        self.assertEqual(proj.status_code, 201)
        self.assertEqual(self.client.post("/projects/P/default").status_code, 200)

        # 5) synchronous run (queued=false) -> pauses at approval
        run = self.client.post(
            "/runs", json={"prompt": "Improve the project", "project": "P", "max_loops": 3, "queued": False}
        )
        self.assertEqual(run.status_code, 200)
        run_id = run.json()["id"]
        self.assertEqual(run.json()["status"], "WAITING_APPROVAL")

        # 6) get run detail
        detail = self.client.get(f"/runs/{run_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertGreaterEqual(len(detail.json()["steps"]), 1)

        # 7) approve-next, then 8) reject-next (valid: still waiting after the approved step)
        self.assertEqual(self.client.post(f"/runs/{run_id}/approve-next").status_code, 200)
        rejected = self.client.post(f"/runs/{run_id}/reject-next")
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()["status"], "STOPPED")

        # 9) queued run -> 10) appears in the queue -> 11) cancel the queued job
        queued = self.client.post("/runs", json={"prompt": "queued run", "project": "P", "queued": True})
        self.assertEqual(queued.status_code, 200)
        queued_id = queued.json()["id"]
        self.assertEqual(queued.json()["queue_status"], "QUEUED")
        self.assertGreaterEqual(len(self.client.get("/queue").json()), 1)
        cancel = self.client.post(f"/queue/{queued_id}/cancel")
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(storage.get_job_by_run_id(self.db, queued_id).status, "CANCELLED")

        # 12) artifacts for the executed run
        artifacts = self.client.get(f"/runs/{run_id}/artifacts")
        self.assertEqual(artifacts.status_code, 200)
        self.assertIn("runner_stdout", [a["type"] for a in artifacts.json()])

        # 13) provider profiles: seed + list (mock always available; no external CLI needed)
        self.assertEqual(self.client.post("/providers/seed").status_code, 200)
        self.assertIn("mock", [p["name"] for p in self.client.get("/providers").json()])

        # 14) search finds the run by prompt text
        found = self.client.get("/search/runs", params={"q": "Improve"}).json()
        self.assertTrue(any(r["id"] == run_id for r in found))

        # 15) compare two runs + 16) view the prompt chain
        cmp = self.client.get("/compare/runs", params={"run_a": run_id, "run_b": queued_id})
        self.assertEqual(cmp.status_code, 200)
        self.assertIn("summary", cmp.json())
        chain = self.client.get(f"/chains/runs/{run_id}")
        self.assertEqual(chain.status_code, 200)
        self.assertEqual(chain.json()["run_id"], run_id)

        # 17) export the data, then import it into a fresh database
        payload = self.client.post("/export-import/export", json={}).json()
        self.assertEqual(payload["format"], "autoprompt-runner-export")
        summary = self.client.post("/export-import/summary", json={"payload": payload}).json()
        self.assertGreaterEqual(summary["counts"]["runs"], 1)
        other = os.path.join(self._tmp.name, "imported.db")
        storage.init_db(other)
        app.dependency_overrides[get_db_path] = lambda: other
        try:
            result = self.client.post("/export-import/import", json={"payload": payload, "mode": "merge"})
            self.assertEqual(result.status_code, 200)
            self.assertGreater(result.json()["imported"], 0)
        finally:
            app.dependency_overrides[get_db_path] = lambda: self.db


if __name__ == "__main__":
    unittest.main()
