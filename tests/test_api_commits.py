"""Tests for the local commit API (FastAPI TestClient + temp DB + a real Git repo)."""

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

    from autoprompt_runner import storage
    from autoprompt_runner.api.app import app
    from autoprompt_runner.api.dependencies import get_db_path
    from autoprompt_runner.state import RunStatus

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False


def _git(path, *args):
    return subprocess.run(["git", *_GIT_ENV, *args], cwd=path, capture_output=True, text=True)


def _clear_auth_env():
    for name in _AUTH_ENV:
        os.environ.pop(name, None)


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class CommitApiTests(unittest.TestCase):
    def setUp(self):
        _clear_auth_env()
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.ws = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.ws)
        _git(self.ws, "init", "-q")
        self._write("README.md", "seed\n")
        _git(self.ws, "add", ".")
        _git(self.ws, "commit", "-q", "-m", "init")
        self.run_id = self._done_run()
        self._write("README.md", "seed\nedited\n")  # an uncommitted change to commit
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        _clear_auth_env()
        self._tmp.cleanup()

    def _write(self, name, content):
        with open(os.path.join(self.ws, name), "w", encoding="utf-8") as handle:
            handle.write(content)

    def _done_run(self, workspace=None):
        rid = storage.create_run(
            self.db, root_prompt="Add a feature", provider="mock", max_loops=1, require_approval=False,
            workspace=workspace if workspace is not None else self.ws,
        )
        storage.update_run_status(self.db, rid, RunStatus.RUNNING.value)
        storage.update_run_status(self.db, rid, RunStatus.DONE.value)
        return rid

    def test_review(self):
        resp = self.client.get(f"/commits/runs/{self.run_id}/review")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ready"])
        self.assertIn("README.md", body["changed_files"])
        self.assertTrue(body["proposed_message"])

    def test_review_missing_run_404(self):
        self.assertEqual(self.client.get("/commits/runs/9999/review").status_code, 404)

    def test_propose(self):
        resp = self.client.post(f"/commits/runs/{self.run_id}/propose")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "PROPOSED")

    def test_apply_requires_confirm(self):
        self.assertEqual(
            self.client.post(f"/commits/runs/{self.run_id}/apply", json={"confirm": False}).status_code, 400
        )
        self.assertEqual(self.client.post(f"/commits/runs/{self.run_id}/apply").status_code, 400)

    def test_apply_missing_run_404(self):
        self.assertEqual(
            self.client.post("/commits/runs/9999/apply", json={"confirm": True}).status_code, 404
        )

    def test_apply_no_changes_400(self):
        clean_run = self._done_run()  # the repo change belongs to run_id; this run sees none after commit
        # First commit the pending change so the workspace is clean.
        self.client.post(f"/commits/runs/{self.run_id}/apply", json={"confirm": True})
        resp = self.client.post(f"/commits/runs/{clean_run}/apply", json={"confirm": True})
        self.assertEqual(resp.status_code, 400)

    def test_apply_blocked_by_secret_file_409(self):
        self._write(".env", "SECRET=1\n")
        resp = self.client.post(f"/commits/runs/{self.run_id}/apply", json={"confirm": True})
        self.assertEqual(resp.status_code, 409)

    def test_apply_success(self):
        resp = self.client.post(f"/commits/runs/{self.run_id}/apply", json={"confirm": True})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["committed"])
        self.assertTrue(body["commit_hash"])
        # The commit is recorded and listable.
        listed = self.client.get(f"/commits/runs/{self.run_id}").json()
        self.assertEqual(listed[0]["status"], "COMMITTED")

    def test_auth_required_when_enabled(self):
        os.environ["AUTOPROMPT_AUTH_ENABLED"] = "true"
        os.environ["AUTOPROMPT_API_TOKEN"] = "secret-token"
        self.assertEqual(self.client.get(f"/commits/runs/{self.run_id}/review").status_code, 401)
        self.assertEqual(
            self.client.post(f"/commits/runs/{self.run_id}/apply", json={"confirm": True}).status_code, 401
        )
        headers = {"Authorization": "Bearer secret-token"}
        self.assertEqual(
            self.client.get(f"/commits/runs/{self.run_id}/review", headers=headers).status_code, 200
        )


if __name__ == "__main__":
    unittest.main()
