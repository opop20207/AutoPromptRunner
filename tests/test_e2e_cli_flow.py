"""End-to-end CLI flow for the v0.1 MVP, exercised entirely with MockRunner.

Walks the full local workflow in one in-process run -- init-db, templates, a project
profile, direct and template runs, approve/reject, listing, artifacts, safety checks, the
queue + worker, cancellation, and config validation -- so a single test proves the pieces
fit together. No real Claude Code / Codex CLI and no network are required.

Runnable via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import storage  # noqa: E402
from autoprompt_runner.cli import main  # noqa: E402
from autoprompt_runner.state import RunStatus  # noqa: E402

_GIT_ENV = ["-c", "user.email=t@example.com", "-c", "user.name=test"]


def _run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class E2ECliFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        self.ws = os.path.join(self._tmp.name, "workspace")
        os.makedirs(self.ws)
        subprocess.run(["git", *_GIT_ENV, "init", "-q"], cwd=self.ws, capture_output=True, text=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _latest_run_id(self):
        return storage.list_runs(self.db)[0].id

    def _ok(self, argv):
        code, out, err = _run_cli([*argv, "--db-path", self.db] if "--db-path" not in argv else argv)
        self.assertEqual(code, 0, f"{argv} -> exit {code}: {err}")
        return out, err

    def test_full_cli_flow(self):
        # 1) init-db
        self._ok(["init-db"])
        self.assertTrue(os.path.exists(self.db))

        # 2) template seed
        self._ok(["template", "seed"])
        self.assertIn("Fix failing tests", [t.name for t in storage.list_templates(self.db)])

        # 3) project add (temporary workspace) + 4) set-default
        self._ok(["project", "add", "--name", "P", "--repo-path", self.ws, "--provider", "mock", "--max-loops", "3"])
        self._ok(["project", "set-default", "--name", "P"])

        # 5) run from a direct prompt (uses the git workspace -> git artifacts + a lock)
        out, _ = self._ok(["run", "--prompt", "Improve the project", "--workspace", self.ws, "--max-loops", "3"])
        self.assertIn("WAITING_APPROVAL", out)
        run_a = self._latest_run_id()

        # 6) run from a template (uses the default project)
        self._ok(["run", "--template", "Continue next task", "--goal", "Tidy up", "--max-loops", "3"])
        run_b = self._latest_run_id()

        # 7) approve-next (advances run A) + 8) reject-next (stops run B)
        self._ok(["approve-next", "--run-id", str(run_a)])
        self._ok(["reject-next", "--run-id", str(run_b)])
        self.assertEqual(storage.get_run(self.db, run_b).status, RunStatus.STOPPED.value)

        # 9) list-runs + 10) show-run
        out, _ = self._ok(["list-runs"])
        self.assertIn(str(run_a), out)
        out, _ = self._ok(["show-run", "--id", str(run_a)])
        self.assertIn("WAITING_APPROVAL", out)

        # 11) show-artifacts (git capture happened for the workspace run)
        out, _ = self._ok(["show-artifacts", "--run-id", str(run_a)])
        self.assertIn("git_status_before", out)
        self.assertIn("runner_stdout", out)

        # 12) safety-check (clean passes; a destructive prompt is blocked)
        code, out, _ = _run_cli(["safety-check", "--prompt", "Improve the README and add tests"])
        self.assertEqual(code, 0)
        code, out, _ = _run_cli(["safety-check", "--prompt", "then run rm -rf / on the repo"])
        self.assertNotEqual(code, 0)

        # 13) queue two runs (max-loops 1 so the worker drives run C straight to DONE)
        self._ok(["run", "--prompt", "queued run C", "--queued", "--max-loops", "1"])
        run_c = self._latest_run_id()
        self._ok(["run", "--prompt", "queued run D", "--queued", "--max-loops", "1"])
        run_d = self._latest_run_id()
        self.assertEqual(storage.get_job_by_run_id(self.db, run_c).status, storage.QUEUE_QUEUED)

        # 15) cancel a queued run (D), then 14) the worker executes the remaining one (C)
        self._ok(["run", "cancel", "--run-id", str(run_d)])
        self.assertEqual(storage.get_run(self.db, run_d).status, RunStatus.STOPPED.value)
        out, _ = self._ok(["worker", "run", "--once"])
        self.assertIn("executed one job", out)
        self.assertEqual(storage.get_run(self.db, run_c).status, RunStatus.DONE.value)
        self.assertEqual(storage.get_job_by_run_id(self.db, run_c).status, storage.QUEUE_DONE)

        # 16) config validate
        code, out, _ = _run_cli(["config", "validate"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
