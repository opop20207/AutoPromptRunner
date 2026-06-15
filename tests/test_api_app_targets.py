"""Tests for the app-target API (FastAPI TestClient + temp DB override)."""

from __future__ import annotations

import os
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

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False


def _clear_auth_env():
    for name in _AUTH_ENV:
        os.environ.pop(name, None)


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class AppTargetApiTests(unittest.TestCase):
    def setUp(self):
        _clear_auth_env()
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        _clear_auth_env()
        self._tmp.cleanup()

    def _create(self, name="FC"):
        return self.client.post("/app-targets", json={"name": name, "session_label": "FactoryColony"})

    def test_create_and_get(self):
        resp = self._create()
        self.assertEqual(resp.status_code, 200)
        target_id = resp.json()["id"]
        self.assertEqual(resp.json()["status"], "ACTIVE")
        self.assertEqual(self.client.get(f"/app-targets/{target_id}").status_code, 200)

    def test_list(self):
        self._create("A")
        self._create("B")
        self.assertEqual(len(self.client.get("/app-targets").json()), 2)

    def test_duplicate_409(self):
        self._create("dup")
        self.assertEqual(self._create("dup").status_code, 409)

    def test_invalid_enum_400(self):
        self.assertEqual(
            self.client.post("/app-targets", json={"name": "x", "submit_mode": "bogus"}).status_code, 400
        )

    def test_get_missing_404(self):
        self.assertEqual(self.client.get("/app-targets/9999").status_code, 404)

    def test_patch(self):
        tid = self._create().json()["id"]
        resp = self.client.patch(f"/app-targets/{tid}", json={"submit_mode": "paste_and_enter"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["submit_mode"], "paste_and_enter")

    def test_enable_disable(self):
        tid = self._create().json()["id"]
        self.assertEqual(self.client.post(f"/app-targets/{tid}/disable").json()["status"], "DISABLED")
        self.assertEqual(self.client.post(f"/app-targets/{tid}/enable").json()["status"], "ACTIVE")

    def test_delete(self):
        tid = self._create().json()["id"]
        self.assertEqual(self.client.delete(f"/app-targets/{tid}").status_code, 200)
        self.assertEqual(self.client.get(f"/app-targets/{tid}").status_code, 404)

    def test_create_with_verification_fields(self):
        resp = self.client.post("/app-targets", json={
            "name": "verified", "verification_mode": "window_title_hint", "expected_window_title": "FactoryColony",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["verification_mode"], "window_title_hint")
        self.assertTrue(body["target_fingerprint"])

    def test_active_window_endpoint(self):
        resp = self.client.get("/app-targets/active-window")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("available", resp.json())
        self.assertIn("platform", resp.json())

    def test_verify_endpoint(self):
        tid = self._create().json()["id"]  # default manual_confirm
        resp = self.client.post(f"/app-targets/{tid}/verify")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "manual_required")
        # Persisted on the target.
        self.assertEqual(self.client.get(f"/app-targets/{tid}").json()["last_verification_status"], "manual_required")

    def test_verify_missing_404(self):
        self.assertEqual(self.client.post("/app-targets/9999/verify").status_code, 404)

    def test_auth_required_when_enabled(self):
        os.environ["AUTOPROMPT_AUTH_ENABLED"] = "true"
        os.environ["AUTOPROMPT_API_TOKEN"] = "secret-token"
        self.assertEqual(self.client.get("/app-targets").status_code, 401)
        self.assertEqual(self.client.get("/app-targets/active-window").status_code, 401)
        self.assertEqual(
            self.client.get("/app-targets", headers={"Authorization": "Bearer secret-token"}).status_code, 200
        )


if __name__ == "__main__":
    unittest.main()
