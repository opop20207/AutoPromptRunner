"""Tests for the checkpoint / rollback API (FastAPI TestClient + temp DB + a real Git repo)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_AUTH_ENV = ("AUTOPROMPT_AUTH_ENABLED", "AUTOPROMPT_API_TOKEN", "AUTOPROMPT_ALLOW_UNAUTHENTICATED_HEALTH")
_GIT_ENV = ["-c", "user.email=t@example.com", "-c", "user.name=test"]

try:
    from fastapi.testclient import TestClient

    from autoprompt_runner import checkpoints, storage
    from autoprompt_runner.api.app import app
    from autoprompt_runner.api.dependencies import get_db_path

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False


def _git(path, *args):
    return subprocess.run(["git", *_GIT_ENV, *args], cwd=path, capture_output=True, text=True)


def _clear_auth_env():
    for name in _AUTH_ENV:
        os.environ.pop(name, None)


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class CheckpointApiTests(unittest.TestCase):
    def setUp(self):
        _clear_auth_env()
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.ws = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.ws)
        _git(self.ws, "init", "-q")
        self._write("f.txt", "one\n")
        _git(self.ws, "add", ".")
        _git(self.ws, "commit", "-q", "-m", "init")
        self.run_id = storage.create_run(
            self.db, root_prompt="p", provider="mock", max_loops=1, require_approval=False, workspace=self.ws
        )
        self.cp = checkpoints.create_checkpoint(self.db, self.run_id, None, self.ws)
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        _clear_auth_env()
        self._tmp.cleanup()

    def _write(self, name, content):
        with open(os.path.join(self.ws, name), "w", encoding="utf-8") as handle:
            handle.write(content)

    def _read(self, name):
        with open(os.path.join(self.ws, name), encoding="utf-8") as handle:
            return handle.read()

    def test_list_for_run(self):
        resp = self.client.get(f"/checkpoints/runs/{self.run_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], self.cp.id)
        self.assertEqual(body[0]["status"], "CREATED")

    def test_list_missing_run_404(self):
        self.assertEqual(self.client.get("/checkpoints/runs/9999").status_code, 404)

    def test_get_checkpoint(self):
        resp = self.client.get(f"/checkpoints/{self.cp.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["git_head_before"], self.cp.git_head_before)

    def test_get_missing_checkpoint_404(self):
        self.assertEqual(self.client.get("/checkpoints/9999").status_code, 404)

    def test_rollback_plan(self):
        self._write("f.txt", "EDIT\n")
        resp = self.client.get(f"/checkpoints/{self.cp.id}/rollback-plan")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["can_rollback"])
        self.assertTrue(body["safe"])
        self.assertEqual(self._read("f.txt"), "EDIT\n")  # plan changed nothing

    def test_rollback_requires_confirm(self):
        self.assertEqual(
            self.client.post(f"/checkpoints/{self.cp.id}/rollback", json={"confirm": False}).status_code, 400
        )
        # Missing body also defaults confirm=false -> 400.
        self.assertEqual(self.client.post(f"/checkpoints/{self.cp.id}/rollback").status_code, 400)

    def test_rollback_missing_checkpoint_404(self):
        self.assertEqual(
            self.client.post("/checkpoints/9999/rollback", json={"confirm": True}).status_code, 404
        )

    def test_rollback_success(self):
        self._write("f.txt", "EDIT\n")
        resp = self.client.post(f"/checkpoints/{self.cp.id}/rollback", json={"confirm": True})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["restored"])
        self.assertEqual(self._read("f.txt"), "one\n")  # workspace restored

    def test_rollback_unsafe_without_force_409(self):
        # A pre-existing checkpoint that captured a dirty workspace -> unsafe without force.
        self._write("pre.txt", "preexisting\n")
        dirty_cp = checkpoints.create_checkpoint(self.db, self.run_id, None, self.ws)
        resp = self.client.post(f"/checkpoints/{dirty_cp.id}/rollback", json={"confirm": True, "force": False})
        self.assertEqual(resp.status_code, 409)
        ok = self.client.post(f"/checkpoints/{dirty_cp.id}/rollback", json={"confirm": True, "force": True})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["restored"])

    def test_auth_required_when_enabled(self):
        os.environ["AUTOPROMPT_AUTH_ENABLED"] = "true"
        os.environ["AUTOPROMPT_API_TOKEN"] = "secret-token"
        self.assertEqual(self.client.get(f"/checkpoints/runs/{self.run_id}").status_code, 401)
        self.assertEqual(
            self.client.post(f"/checkpoints/{self.cp.id}/rollback", json={"confirm": True}).status_code, 401
        )
        headers = {"Authorization": "Bearer secret-token"}
        self.assertEqual(self.client.get(f"/checkpoints/runs/{self.run_id}", headers=headers).status_code, 200)


if __name__ == "__main__":
    unittest.main()
