"""Tests for the compare API endpoints (FastAPI TestClient + temp DB override)."""

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
class CompareApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.run_a = storage.create_run(
            self.db, root_prompt="Fix placement preview", provider="mock", max_loops=1, require_approval=False
        )
        step_a = storage.create_step(
            self.db, self.run_a, 0, "run tests", "FAILED", exit_code=1, next_prompt="Fix next"
        )
        storage.create_artifact(
            self.db, self.run_a, ArtifactType.CHANGED_FILES.value,
            content="src/app.py\nsrc/preview.py", step_id=step_a,
        )
        storage.create_artifact(
            self.db, self.run_a, ArtifactType.GIT_DIFF_STAT.value, content="2 files changed", step_id=step_a
        )
        self.run_b = storage.create_run(
            self.db, root_prompt="Update docs", provider="codex", max_loops=1, require_approval=False
        )
        step_b = storage.create_step(self.db, self.run_b, 0, "edit docs", "DONE", exit_code=0)
        storage.create_artifact(
            self.db, self.run_b, ArtifactType.CHANGED_FILES.value, content="src/app.py\nREADME.md", step_id=step_b
        )
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self._tmp.cleanup()

    def test_compare_runs(self):
        resp = self.client.get("/compare/runs", params={"run_a": self.run_a, "run_b": self.run_b})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["run_a"]["id"], self.run_a)
        self.assertEqual(body["run_b"]["id"], self.run_b)
        self.assertFalse(body["same_provider"])
        self.assertEqual(body["changed_files"]["common"], ["src/app.py"])
        self.assertEqual(body["changed_files"]["only_a"], ["src/preview.py"])
        self.assertEqual(body["steps"]["failed_steps_a"], 1)
        self.assertIn("changed_files", body["artifact_counts_by_type_a"]["counts"])

    def test_compare_missing_run_returns_404(self):
        resp = self.client.get("/compare/runs", params={"run_a": self.run_a, "run_b": 9999})
        self.assertEqual(resp.status_code, 404)

    def test_compare_same_run_returns_400(self):
        resp = self.client.get("/compare/runs", params={"run_a": self.run_a, "run_b": self.run_a})
        self.assertEqual(resp.status_code, 400)

    def test_compare_show_prompts(self):
        resp = self.client.get(
            "/compare/runs", params={"run_a": self.run_a, "run_b": self.run_b, "show_prompts": "true"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["run_a"]["root_prompt"], "Fix placement preview")

    def test_compare_show_artifacts_false_omits_counts(self):
        resp = self.client.get(
            "/compare/runs", params={"run_a": self.run_a, "run_b": self.run_b, "show_artifacts": "false"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["artifact_counts_by_type_a"]["counts"], {})


if __name__ == "__main__":
    unittest.main()
