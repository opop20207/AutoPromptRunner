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
        self.assertEqual(resp.json(), {"status": "ok", "service": "AutoPromptRunner"})


if __name__ == "__main__":
    unittest.main()
