"""Tests for the events API: the JSON event list and the SSE stream (auth-aware)."""

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
    from autoprompt_runner.services.run_service import RunService

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False


def _clear_auth_env():
    for name in _AUTH_ENV:
        os.environ.pop(name, None)


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class EventApiTests(unittest.TestCase):
    def setUp(self):
        _clear_auth_env()
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        # A finished run so the SSE stream replays its events and then closes (no hang).
        self.run_id = RunService(self.db).start("do it", "mock", max_loops=1, require_approval=False).run_id
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        _clear_auth_env()
        self._tmp.cleanup()

    def _enable_auth(self, token="secret-token"):
        os.environ["AUTOPROMPT_AUTH_ENABLED"] = "true"
        os.environ["AUTOPROMPT_API_TOKEN"] = token

    def test_list_events_json(self):
        resp = self.client.get(f"/events/runs/{self.run_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        types = [e["type"] for e in body["events"]]
        self.assertIn("run_created", types)
        self.assertIn("run_done", types)
        self.assertIsNotNone(body["latest_id"])

    def test_list_events_missing_run_404(self):
        self.assertEqual(self.client.get("/events/runs/9999").status_code, 404)

    def test_stream_missing_run_404(self):
        self.assertEqual(self.client.get("/events/runs/9999/stream").status_code, 404)

    def test_stream_returns_sse_for_terminal_run(self):
        resp = self.client.get(f"/events/runs/{self.run_id}/stream")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
        body = resp.text
        self.assertIn("event: run_created", body)
        self.assertIn("event: run_done", body)

    def test_stream_after_id(self):
        events = self.client.get(f"/events/runs/{self.run_id}").json()["events"]
        first_id = events[0]["id"]
        body = self.client.get(f"/events/runs/{self.run_id}/stream", params={"after_id": first_id}).text
        # The first event is excluded; later events remain.
        self.assertNotIn(f"id: {first_id}\n", body)
        self.assertIn("event: run_done", body)

    def test_auth_required_when_enabled(self):
        self._enable_auth()
        self.assertEqual(self.client.get(f"/events/runs/{self.run_id}").status_code, 401)
        self.assertEqual(self.client.get(f"/events/runs/{self.run_id}/stream").status_code, 401)

    def test_stream_accepts_token_via_query(self):
        self._enable_auth(token="secret-token")
        resp = self.client.get(f"/events/runs/{self.run_id}/stream", params={"token": "secret-token"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("event: run_done", resp.text)

    def test_stream_accepts_token_via_header(self):
        self._enable_auth(token="secret-token")
        resp = self.client.get(
            f"/events/runs/{self.run_id}/stream", headers={"Authorization": "Bearer secret-token"}
        )
        self.assertEqual(resp.status_code, 200)

    def test_stream_rejects_bad_query_token(self):
        self._enable_auth(token="secret-token")
        self.assertEqual(
            self.client.get(f"/events/runs/{self.run_id}/stream", params={"token": "wrong"}).status_code, 401
        )


if __name__ == "__main__":
    unittest.main()
