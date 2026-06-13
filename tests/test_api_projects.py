"""Tests for the project API endpoints (FastAPI TestClient + temp DB override)."""

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
class ProjectApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.repo)
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self._tmp.cleanup()

    def _create(self, name="FactoryColony", provider="mock"):
        return self.client.post(
            "/projects",
            json={
                "name": name, "repo_path": self.repo, "default_provider": provider,
                "default_max_loops": 5, "require_approval": True, "timeout_seconds": 1800,
            },
        )

    def test_create_project(self):
        resp = self._create()
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["name"], "FactoryColony")
        self.assertEqual(body["default_provider"], "mock")
        self.assertEqual(body["default_max_loops"], 5)
        self.assertFalse(body["is_default"])

    def test_create_invalid_provider_returns_400(self):
        resp = self.client.post("/projects", json={"name": "X", "repo_path": self.repo, "default_provider": "nope"})
        self.assertEqual(resp.status_code, 400)

    def test_create_invalid_repo_path_returns_400(self):
        bad = os.path.join(self.repo, "no-such-dir")
        resp = self.client.post("/projects", json={"name": "X", "repo_path": bad})
        self.assertEqual(resp.status_code, 400)

    def test_list_projects(self):
        self._create(name="A")
        self._create(name="B")
        resp = self.client.get("/projects")
        self.assertEqual(resp.status_code, 200)
        names = [p["name"] for p in resp.json()]
        self.assertIn("A", names)
        self.assertIn("B", names)

    def test_get_project(self):
        self._create(name="P")
        resp = self.client.get("/projects/P")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["name"], "P")

    def test_get_missing_project_returns_404(self):
        self.assertEqual(self.client.get("/projects/missing").status_code, 404)

    def test_set_default(self):
        self._create(name="P")
        resp = self.client.post("/projects/P/default")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["default_project"], "P")
        self.assertTrue(self.client.get("/projects/P").json()["is_default"])

    def test_delete_project_keeps_files(self):
        marker = os.path.join(self.repo, "keep.txt")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("x")
        self._create(name="P")
        resp = self.client.delete("/projects/P")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["files_deleted"])
        self.assertTrue(os.path.exists(marker))  # files on disk untouched
        self.assertEqual(self.client.get("/projects/P").status_code, 404)


if __name__ == "__main__":
    unittest.main()
