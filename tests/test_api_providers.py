"""Tests for the provider profile API endpoints (FastAPI TestClient + temp DB override)."""

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

    from autoprompt_runner import providers, storage
    from autoprompt_runner.api.app import app
    from autoprompt_runner.api.dependencies import get_db_path

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class ProviderApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self._tmp.cleanup()

    def test_seed_and_list(self):
        resp = self.client.post("/providers/seed")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["seeded"], 3)
        listed = self.client.get("/providers").json()
        self.assertEqual({p["name"] for p in listed}, {"mock", "claude-code", "codex"})

    def test_crud(self):
        created = self.client.post(
            "/providers",
            json={"name": "claude-fast", "type": "claude-code", "command": "claude", "default_timeout_seconds": 1200},
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["type"], "claude-code")
        got = self.client.get("/providers/claude-fast")
        self.assertEqual(got.status_code, 200)
        self.assertIn("available", got.json())
        patched = self.client.patch("/providers/claude-fast", json={"default_timeout_seconds": 1800})
        self.assertEqual(patched.json()["default_timeout_seconds"], 1800)
        deleted = self.client.delete("/providers/claude-fast")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/providers/claude-fast").status_code, 404)

    def test_enable_disable(self):
        self.client.post("/providers/seed")
        self.assertFalse(self.client.post("/providers/mock/disable").json()["enabled"])
        self.assertTrue(self.client.post("/providers/mock/enable").json()["enabled"])

    def test_create_validation_errors(self):
        self.assertEqual(self.client.post("/providers", json={"name": "z", "type": "nope", "command": "x"}).status_code, 400)
        self.client.post("/providers", json={"name": "dup", "type": "mock", "command": "mock"})
        self.assertEqual(self.client.post("/providers", json={"name": "dup", "type": "mock", "command": "mock"}).status_code, 400)

    def test_check_availability(self):
        self.client.post("/providers/seed")
        mock = self.client.get("/providers/mock/check").json()
        self.assertTrue(mock["available"])
        claude = self.client.get("/providers/claude-code/check").json()
        self.assertFalse(claude["available"])  # 'claude' not installed in tests
        self.assertEqual(self.client.get("/providers/nope/check").status_code, 404)

    def test_run_rejects_disabled_provider(self):
        self.client.post("/providers", json={"name": "mock-off", "type": "mock", "command": "mock", "enabled": False})
        resp = self.client.post(
            "/runs", json={"prompt": "hi", "provider": "mock-off", "queued": False, "require_approval": False}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("disabled", resp.json()["detail"])

    def test_run_rejects_unavailable_external_provider(self):
        self.client.post(
            "/providers",
            json={"name": "claude-x", "type": "claude-code", "command": "definitely-not-a-real-cli-xyz"},
        )
        resp = self.client.post(
            "/runs", json={"prompt": "hi", "provider": "claude-x", "queued": False, "require_approval": False}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not available", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
