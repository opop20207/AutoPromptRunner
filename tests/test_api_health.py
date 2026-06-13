"""Tests for the API health endpoint (FastAPI TestClient)."""

from __future__ import annotations

import os
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from fastapi.testclient import TestClient

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover - exercised only when the api extra is absent
    _HAVE_FASTAPI = False


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class HealthApiTests(unittest.TestCase):
    def test_health_ok(self):
        from autoprompt_runner.api.app import app

        client = TestClient(app)
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], "AutoPromptRunner")

    def test_health_includes_safe_config_metadata(self):
        from autoprompt_runner.api.app import app

        config = TestClient(app).get("/health").json()["config"]
        for key in (
            "db_path",
            "default_provider",
            "queue_poll_interval_seconds",
            "max_loops_hard_limit",
            "timeout_seconds_hard_limit",
        ):
            self.assertIn(key, config)
        # No environment dumps / secrets in the metadata.
        self.assertNotIn("env", config)
        self.assertFalse(any("secret" in str(k).lower() for k in config))


if __name__ == "__main__":
    unittest.main()
