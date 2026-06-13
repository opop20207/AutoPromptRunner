"""Tests for the search API endpoints (FastAPI TestClient + temp DB override)."""

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
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class SearchApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.run1 = storage.create_run(
            self.db, root_prompt="Fix the failing PlacementPreview tests", provider="mock",
            max_loops=1, require_approval=False,
        )
        step = storage.create_step(self.db, self.run1, 0, "run tests", "DONE", stderr="Traceback boom")
        storage.create_artifact(
            self.db, self.run1, "runner_stderr", content="Traceback (most recent call last)", step_id=step
        )
        storage.create_run(self.db, root_prompt="Update docs", provider="codex", max_loops=1, require_approval=False)
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self._tmp.cleanup()

    def test_search_runs(self):
        resp = self.client.get("/search/runs", params={"q": "placement"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.run1, [r["id"] for r in resp.json()])

    def test_search_runs_provider_filter(self):
        resp = self.client.get("/search/runs", params={"provider": "codex"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(all(r["provider"] == "codex" for r in resp.json()))

    def test_search_runs_empty_query_returns_list(self):
        resp = self.client.get("/search/runs")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.json()), 2)

    def test_search_artifacts(self):
        resp = self.client.get("/search/artifacts", params={"q": "Traceback", "type": "runner_stderr"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body)
        self.assertTrue(all(a["type"] == "runner_stderr" for a in body))

    def test_search_all_grouped(self):
        resp = self.client.get("/search/all", params={"q": "Traceback"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("runs", body)
        self.assertIn("steps", body)
        self.assertIn("artifacts", body)
        self.assertTrue(any(s["run_id"] == self.run1 for s in body["steps"]))


if __name__ == "__main__":
    unittest.main()
