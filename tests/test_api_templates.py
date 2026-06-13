"""Tests for the template API endpoints + run-from-template (FastAPI TestClient)."""

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


class _ApiTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self._tmp.cleanup()


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class TemplateApiTests(_ApiTestCase):
    def test_seed_and_list(self):
        resp = self.client.post("/templates/seed")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.json()["seeded"], 1)
        names = [t["name"] for t in self.client.get("/templates").json()]
        self.assertIn("Fix failing tests", names)

    def test_create_get_and_duplicate(self):
        resp = self.client.post("/templates", json={"name": "Custom", "body": "Do {{goal}}", "tags": ["x"]})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["tags"], ["x"])
        self.assertEqual(self.client.get("/templates/Custom").status_code, 200)
        dup = self.client.post("/templates", json={"name": "Custom", "body": "again"})
        self.assertEqual(dup.status_code, 400)

    def test_get_missing_returns_404(self):
        self.assertEqual(self.client.get("/templates/missing").status_code, 404)

    def test_create_empty_body_returns_400(self):
        self.assertEqual(self.client.post("/templates", json={"name": "E", "body": "   "}).status_code, 400)

    def test_delete_template(self):
        self.client.post("/templates", json={"name": "Temp", "body": "x"})
        self.assertEqual(self.client.delete("/templates/Temp").status_code, 200)
        self.assertEqual(self.client.get("/templates/Temp").status_code, 404)

    def test_render_known_and_unknown_placeholders(self):
        self.client.post("/templates", json={"name": "R", "body": "Goal: {{goal}} / {{unknown}}"})
        resp = self.client.post("/templates/R/render", json={"goal": "ship it"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["rendered"], "Goal: ship it / {{unknown}}")

    def test_render_missing_template_returns_404(self):
        self.assertEqual(self.client.post("/templates/nope/render", json={}).status_code, 404)


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class RunFromTemplateApiTests(_ApiTestCase):
    def test_run_from_template(self):
        self.client.post("/templates/seed")
        resp = self.client.post(
            "/runs",
            json={
                "template": "Fix failing tests",
                "goal": "Fix failing placement preview tests",
                "max_loops": 1,
                "require_approval": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "DONE")
        detail = self.client.get(f"/runs/{body['id']}").json()
        self.assertIn("Fix failing placement preview tests", detail["prompt"])

    def test_run_rejects_prompt_and_template_together(self):
        self.client.post("/templates/seed")
        resp = self.client.post("/runs", json={"prompt": "p", "template": "Fix failing tests"})
        self.assertEqual(resp.status_code, 400)

    def test_run_missing_template_returns_404(self):
        self.assertEqual(self.client.post("/runs", json={"template": "nope"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
