"""Tests for the export/import API endpoints (FastAPI TestClient + temp DB override)."""

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
    from autoprompt_runner.state import RunStatus

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class ExportImportApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self._tmp.name, "src.db")
        self.dst = os.path.join(self._tmp.name, "dst.db")
        storage.init_db(self.src)
        storage.init_db(self.dst)
        storage.create_template(self.src, name="Cont", body="do {{goal}}", tags=["x"])
        run_id = storage.create_run(self.src, root_prompt="Fix it", provider="mock", max_loops=1, require_approval=False)
        storage.create_step(self.src, run_id, 0, "run", "FAILED", stderr="boom", exit_code=1)
        storage.update_run_status(self.src, run_id, RunStatus.FAILED.value)
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self._tmp.cleanup()

    def _use(self, db):
        app.dependency_overrides[get_db_path] = lambda: db

    def _export(self):
        self._use(self.src)
        return self.client.post("/export-import/export", json={}).json()

    def test_export(self):
        payload = self._export()
        self.assertEqual(payload["format"], "autoprompt-runner-export")
        self.assertEqual(len(payload["data"]["runs"]), 1)
        self.assertEqual(len(payload["data"]["templates"]), 1)

    def test_export_selected_run_ids(self):
        self._use(self.src)
        payload = self.client.post("/export-import/export", json={"run_ids": [9999]}).json()
        self.assertEqual(payload["data"]["runs"], [])

    def test_summary(self):
        payload = self._export()
        resp = self.client.post("/export-import/summary", json={"payload": payload})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["counts"]["runs"], 1)

    def test_import(self):
        payload = self._export()
        self._use(self.dst)
        resp = self.client.post("/export-import/import", json={"payload": payload, "mode": "merge"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertGreater(body["imported"], 0)
        self.assertEqual(body["mode"], "merge")
        self.assertEqual(len(storage.list_runs(self.dst)), 1)

    def test_import_invalid_payload_returns_400(self):
        self._use(self.dst)
        resp = self.client.post("/export-import/import", json={"payload": {"format": "nope"}, "mode": "merge"})
        self.assertEqual(resp.status_code, 400)

    def test_import_unknown_version_returns_400(self):
        self._use(self.dst)
        resp = self.client.post(
            "/export-import/import",
            json={"payload": {"format": "autoprompt-runner-export", "version": 99, "data": {}}, "mode": "merge"},
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
