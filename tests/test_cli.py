"""Tests for the AutoPromptRunner CLI, including the claude-code provider paths.

Standard-library only (unittest + tempfile + unittest.mock). Every command that
touches the database is given an explicit temporary ``--db-path`` so the tests never
write into the working tree, and the claude-code subprocess is patched so no real
``claude`` executable is required. Runnable via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import __version__, storage  # noqa: E402
from autoprompt_runner.cli import main  # noqa: E402
from autoprompt_runner.state import RunStatus  # noqa: E402

_SUBPROCESS_RUN = "autoprompt_runner.runners.claude_code.subprocess.run"


def run_cli(argv):
    """Invoke the CLI in-process, capturing exit code, stdout, and stderr."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class VersionCommandTests(unittest.TestCase):
    def test_version_command_succeeds(self):
        code, out, err = run_cli(["version"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), __version__)


class _DbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = self._tmp.name
        self.db = os.path.join(self._tmp.name, "autoprompt.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _latest_run_id(self):
        return storage.list_runs(self.db)[0].id


class RunValidationTests(_DbTestCase):
    def test_empty_prompt_is_rejected(self):
        code, out, err = run_cli(["run", "--prompt", "   ", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("prompt", err.lower())
        self.assertFalse(os.path.exists(self.db))  # rejected before touching the DB

    def test_invalid_max_loops_is_rejected(self):
        code, out, err = run_cli(["run", "--prompt", "hello", "--max-loops", "0", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("max-loops", err.lower())

    def test_unsupported_provider_is_rejected(self):
        code, out, err = run_cli(["run", "--prompt", "hello", "--provider", "codex", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("provider", err.lower())

    def test_timeout_seconds_must_be_positive(self):
        code, out, err = run_cli(
            ["run", "--prompt", "hello", "--timeout-seconds", "0", "--db-path", self.db]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("timeout", err.lower())

    def test_claude_code_requires_workspace(self):
        code, out, err = run_cli(["run", "--prompt", "hello", "--provider", "claude-code", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("workspace", err.lower())
        self.assertFalse(os.path.exists(self.db))

    def test_claude_code_rejects_invalid_workspace(self):
        bad = os.path.join(self.ws, "no-such-dir")
        code, out, err = run_cli(
            ["run", "--prompt", "hello", "--provider", "claude-code", "--workspace", bad, "--db-path", self.db]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("workspace", err.lower())


class MockProviderFlowTests(_DbTestCase):
    def test_init_db_command_creates_database(self):
        code, out, err = run_cli(["init-db", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.db))

    def test_run_default_creates_pending_approval(self):
        code, out, err = run_cli(["run", "--prompt", "Improve README", "--max-loops", "3", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("WAITING_APPROVAL", out)
        self.assertIn("next_prompt", out)
        run = storage.get_run(self.db, self._latest_run_id())
        self.assertEqual(run.status, RunStatus.WAITING_APPROVAL.value)
        self.assertIsNotNone(storage.get_pending_approval(self.db, run.id))

    def test_run_no_approval_autoruns_to_done(self):
        code, out, err = run_cli(
            ["run", "--prompt", "p", "--max-loops", "3", "--no-approval", "--db-path", self.db]
        )
        self.assertEqual(code, 0)
        self.assertIn("DONE", out)
        self.assertEqual(len(storage.get_steps_for_run(self.db, self._latest_run_id())), 3)

    def test_approve_next_executes_step(self):
        run_cli(["run", "--prompt", "p", "--max-loops", "3", "--db-path", self.db])
        rid = self._latest_run_id()
        code, out, err = run_cli(["approve-next", "--run-id", str(rid), "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertEqual(len(storage.get_steps_for_run(self.db, rid)), 2)

    def test_reject_next_stops_run(self):
        run_cli(["run", "--prompt", "p", "--max-loops", "3", "--db-path", self.db])
        rid = self._latest_run_id()
        code, out, err = run_cli(["reject-next", "--run-id", str(rid), "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("STOPPED", out)
        self.assertEqual(storage.get_run(self.db, rid).status, RunStatus.STOPPED.value)

    def test_approve_next_no_pending_exits_nonzero(self):
        run_cli(["run", "--prompt", "p", "--max-loops", "1", "--no-approval", "--db-path", self.db])
        rid = self._latest_run_id()
        code, out, err = run_cli(["approve-next", "--run-id", str(rid), "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("error", err.lower())

    def test_reject_next_missing_run_exits_nonzero(self):
        run_cli(["init-db", "--db-path", self.db])
        code, out, err = run_cli(["reject-next", "--run-id", "999", "--db-path", self.db])
        self.assertNotEqual(code, 0)

    def test_show_run_includes_approval_state(self):
        run_cli(["run", "--prompt", "Improve README", "--max-loops", "3", "--db-path", self.db])
        rid = self._latest_run_id()
        code, out, err = run_cli(["show-run", "--id", str(rid), "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("WAITING_APPROVAL", out)
        self.assertIn("Pending approval", out)

    def test_show_run_missing_id_exits_nonzero(self):
        run_cli(["init-db", "--db-path", self.db])
        code, out, err = run_cli(["show-run", "--id", "999", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())


class ClaudeCodeProviderTests(_DbTestCase):
    def test_run_claude_code_command_unavailable_stores_failed(self):
        # Simulate the claude CLI being absent regardless of the test environment.
        with mock.patch(_SUBPROCESS_RUN, side_effect=FileNotFoundError()):
            code, out, err = run_cli(
                [
                    "run", "--prompt", "Review project", "--provider", "claude-code",
                    "--workspace", self.ws, "--max-loops", "1", "--db-path", self.db,
                ]
            )
        self.assertNotEqual(code, 0)  # FAILED -> non-zero exit
        run = storage.get_run(self.db, self._latest_run_id())
        self.assertEqual(run.status, RunStatus.FAILED.value)
        self.assertEqual(run.provider, "claude-code")
        self.assertEqual(run.workspace, self.ws)
        steps = storage.get_steps_for_run(self.db, run.id)
        self.assertEqual(len(steps), 1)
        self.assertNotEqual(steps[0].exit_code, 0)
        self.assertIn("not found", (steps[0].stderr or "").lower())


if __name__ == "__main__":
    unittest.main()
