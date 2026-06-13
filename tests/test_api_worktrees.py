"""Tests for the worktree API endpoints + run-with-worktree (FastAPI TestClient)."""

from __future__ import annotations

import os
import subprocess
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

_GIT_ENV = ["-c", "user.email=t@example.com", "-c", "user.name=test"]


def _init_repo(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", *_GIT_ENV, "init", "-q"], cwd=path, capture_output=True, text=True)
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as handle:
        handle.write("seed\n")
    subprocess.run(["git", *_GIT_ENV, "add", "."], cwd=path, capture_output=True, text=True)
    subprocess.run(["git", *_GIT_ENV, "commit", "-q", "-m", "init"], cwd=path, capture_output=True, text=True)


class _WtApiBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        self.repo = os.path.join(self._tmp.name, "repo")
        _init_repo(self.repo)
        storage.init_db(self.db)
        storage.create_project(
            self.db, name="P", repo_path=self.repo, default_provider="mock",
            default_max_loops=1, require_approval=True, timeout_seconds=1800,
        )
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self._tmp.cleanup()

    def _create(self, name="ui-session", branch=None):
        return self.client.post(
            "/worktrees", json={"project": "P", "name": name, "branch": branch or f"autoprompt/{name}"}
        )


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class WorktreeApiTests(_WtApiBase):
    def test_create_list_show(self):
        resp = self._create()
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["name"], "ui-session")
        self.assertEqual(body["status"], "ACTIVE")
        self.assertEqual(body["project"], "P")
        self.assertIn("ui-session", [w["name"] for w in self.client.get("/worktrees").json()])
        self.assertEqual(self.client.get("/worktrees/ui-session").status_code, 200)

    def test_create_invalid_name_returns_400(self):
        resp = self.client.post("/worktrees", json={"project": "P", "name": "bad/name", "branch": "autoprompt/x"})
        self.assertEqual(resp.status_code, 400)

    def test_create_missing_project_returns_404(self):
        resp = self.client.post("/worktrees", json={"project": "missing", "name": "x", "branch": "autoprompt/x"})
        self.assertEqual(resp.status_code, 404)

    def test_get_missing_returns_404(self):
        self.assertEqual(self.client.get("/worktrees/nope").status_code, 404)

    def test_archive(self):
        self._create()
        resp = self.client.post("/worktrees/ui-session/archive")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ARCHIVED")

    def test_delete_removes_record_and_dir(self):
        self._create()
        wt = storage.get_worktree_by_name(self.db, "ui-session")
        resp = self.client.delete("/worktrees/ui-session")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self.client.get("/worktrees/ui-session").status_code, 404)
        self.assertFalse(os.path.isdir(wt.path))


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class RunWithWorktreeApiTests(_WtApiBase):
    def test_run_uses_worktree_workspace(self):
        self._create()
        resp = self.client.post(
            "/runs", json={"prompt": "p", "worktree": "ui-session", "max_loops": 1, "require_approval": False}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "DONE")
        wt = storage.get_worktree_by_name(self.db, "ui-session")
        detail = self.client.get(f"/runs/{resp.json()['id']}").json()
        self.assertEqual(detail["workspace"], wt.path)

    def test_run_archived_worktree_returns_400(self):
        self._create()
        self.client.post("/worktrees/ui-session/archive")
        resp = self.client.post("/runs", json={"prompt": "p", "worktree": "ui-session"})
        self.assertEqual(resp.status_code, 400)

    def test_run_missing_worktree_returns_404(self):
        self.assertEqual(self.client.post("/runs", json={"prompt": "p", "worktree": "nope"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
