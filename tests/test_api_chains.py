"""Tests for the chains API endpoint (FastAPI TestClient + temp DB override)."""

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
    from autoprompt_runner.artifacts import ArtifactType

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class ChainApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.run_id = storage.create_run(
            self.db, root_prompt="Build the feature", provider="mock", max_loops=3, require_approval=True
        )
        s0 = storage.create_step(
            self.db, self.run_id, 0, "do step 0", "DONE", exit_code=0, next_prompt="continue"
        )
        storage.create_artifact(
            self.db, self.run_id, ArtifactType.CHANGED_FILES.value, content="src/a.py", step_id=s0
        )
        storage.create_approval(self.db, self.run_id, s0, "continue", status="APPROVED")
        self.s1 = storage.create_step(
            self.db, self.run_id, 1, "do step 1", "FAILED", exit_code=1, stderr="boom", next_prompt="fix it"
        )
        storage.create_approval(self.db, self.run_id, self.s1, "fix it")  # PENDING
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self._tmp.cleanup()

    def test_get_chain(self):
        resp = self.client.get(f"/chains/runs/{self.run_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["run_id"], self.run_id)
        self.assertEqual(body["step_count"], 2)
        self.assertTrue(body["pending_approval"])
        self.assertEqual(body["failed_step_count"], 1)
        self.assertEqual(len(body["chain_nodes"]), 2)
        self.assertEqual(body["chain_nodes"][0]["approval_status"], "APPROVED")
        self.assertIn("changed_files", body["chain_nodes"][0]["artifact_counts_by_type"]["counts"])

    def test_get_chain_missing_run_returns_404(self):
        resp = self.client.get("/chains/runs/9999")
        self.assertEqual(resp.status_code, 404)

    def test_get_chain_errors_only(self):
        resp = self.client.get(f"/chains/runs/{self.run_id}", params={"errors_only": "true"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual([n["step_id"] for n in body["chain_nodes"]], [self.s1])
        self.assertEqual(body["step_count"], 2)  # summary still reflects the full run

    def test_get_chain_full_prompts(self):
        resp = self.client.get(f"/chains/runs/{self.run_id}", params={"full_prompts": "true"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["chain_nodes"][0]["prompt"], "do step 0")

    def test_get_chain_include_artifacts_false(self):
        resp = self.client.get(f"/chains/runs/{self.run_id}", params={"include_artifacts": "false"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["chain_nodes"][0]["artifact_counts_by_type"]["counts"], {})


if __name__ == "__main__":
    unittest.main()
