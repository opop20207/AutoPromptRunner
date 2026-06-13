"""Tests for the AutoPromptRunner CLI: validation, mock/claude-code runs, and projects.

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
        code, out, err = run_cli(["run", "--prompt", "hello", "--timeout-seconds", "0", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("timeout", err.lower())

    def test_claude_code_requires_workspace(self):
        code, out, err = run_cli(["run", "--prompt", "hello", "--provider", "claude-code", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("workspace", err.lower())

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
        with mock.patch(_SUBPROCESS_RUN, side_effect=FileNotFoundError()):
            code, out, err = run_cli(
                [
                    "run", "--prompt", "Review project", "--provider", "claude-code",
                    "--workspace", self.ws, "--max-loops", "1", "--db-path", self.db,
                ]
            )
        self.assertNotEqual(code, 0)
        run = storage.get_run(self.db, self._latest_run_id())
        self.assertEqual(run.status, RunStatus.FAILED.value)
        self.assertEqual(run.provider, "claude-code")
        self.assertEqual(run.workspace, self.ws)
        steps = storage.get_steps_for_run(self.db, run.id)
        self.assertEqual(len(steps), 1)
        self.assertNotEqual(steps[0].exit_code, 0)


class ProjectCommandTests(_DbTestCase):
    def _add(self, name="P", provider="mock", max_loops=5, repo=None):
        return run_cli([
            "project", "add", "--name", name, "--repo-path", repo or self.ws,
            "--provider", provider, "--max-loops", str(max_loops), "--db-path", self.db,
        ])

    def test_project_add_and_show(self):
        code, out, err = self._add(name="FactoryColony", provider="mock", max_loops=5)
        self.assertEqual(code, 0)
        project = storage.get_project_by_name(self.db, "FactoryColony")
        self.assertIsNotNone(project)
        self.assertEqual(project.default_max_loops, 5)
        code, out, err = run_cli(["project", "show", "--name", "FactoryColony", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("FactoryColony", out)
        self.assertIn(self.ws, out)

    def test_project_add_invalid_repo_path(self):
        bad = os.path.join(self.ws, "nope")
        code, out, err = run_cli(["project", "add", "--name", "X", "--repo-path", bad, "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("repo-path", err.lower())

    def test_project_add_unsupported_provider(self):
        code, out, err = run_cli(
            ["project", "add", "--name", "X", "--repo-path", self.ws, "--provider", "codex", "--db-path", self.db]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("provider", err.lower())

    def test_project_add_duplicate_name(self):
        self._add(name="X")
        code, out, err = self._add(name="X")
        self.assertNotEqual(code, 0)
        self.assertIn("exists", err.lower())

    def test_project_list_marks_default(self):
        self._add(name="A")
        run_cli(["project", "set-default", "--name", "A", "--db-path", self.db])
        code, out, err = run_cli(["project", "list", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("A", out)
        self.assertIn("*", out)

    def test_project_show_missing_exits_nonzero(self):
        run_cli(["init-db", "--db-path", self.db])
        code, out, err = run_cli(["project", "show", "--name", "nope", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())

    def test_project_delete_clears_default_and_keeps_files(self):
        marker = os.path.join(self.ws, "keep.txt")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("x")
        self._add(name="A")
        run_cli(["project", "set-default", "--name", "A", "--db-path", self.db])
        code, out, err = run_cli(["project", "delete", "--name", "A", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIsNone(storage.get_project_by_name(self.db, "A"))
        self.assertIsNone(storage.get_default_project(self.db))
        self.assertTrue(os.path.exists(marker))  # filesystem untouched


class RunWithProjectTests(_DbTestCase):
    def _add_mock_project(self, name="P", max_loops=4):
        run_cli([
            "project", "add", "--name", name, "--repo-path", self.ws,
            "--provider", "mock", "--max-loops", str(max_loops), "--db-path", self.db,
        ])

    def test_run_uses_selected_project_defaults(self):
        self._add_mock_project(name="P", max_loops=4)
        code, out, err = run_cli(["run", "--project", "P", "--prompt", "Continue", "--db-path", self.db])
        self.assertEqual(code, 0)
        run = storage.get_run(self.db, self._latest_run_id())
        self.assertEqual(run.provider, "mock")
        self.assertEqual(run.max_loops, 4)

    def test_explicit_args_override_project(self):
        self._add_mock_project(name="P", max_loops=4)
        code, out, err = run_cli(
            ["run", "--project", "P", "--prompt", "Continue", "--max-loops", "2", "--db-path", self.db]
        )
        self.assertEqual(code, 0)
        self.assertEqual(storage.get_run(self.db, self._latest_run_id()).max_loops, 2)

    def test_run_uses_default_project_when_no_flag(self):
        self._add_mock_project(name="P", max_loops=6)
        run_cli(["project", "set-default", "--name", "P", "--db-path", self.db])
        code, out, err = run_cli(["run", "--prompt", "Continue", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertEqual(storage.get_run(self.db, self._latest_run_id()).max_loops, 6)

    def test_run_project_not_found_exits_nonzero(self):
        run_cli(["init-db", "--db-path", self.db])
        code, out, err = run_cli(["run", "--project", "missing", "--prompt", "x", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())

    def test_run_claude_code_workspace_from_project(self):
        run_cli([
            "project", "add", "--name", "C", "--repo-path", self.ws,
            "--provider", "claude-code", "--max-loops", "1", "--db-path", self.db,
        ])
        with mock.patch(_SUBPROCESS_RUN, side_effect=FileNotFoundError()):
            code, out, err = run_cli(["run", "--project", "C", "--prompt", "Review", "--db-path", self.db])
        self.assertNotEqual(code, 0)  # claude unavailable -> FAILED
        run = storage.get_run(self.db, self._latest_run_id())
        self.assertEqual(run.provider, "claude-code")
        self.assertEqual(run.workspace, self.ws)  # workspace came from project repo_path
        self.assertEqual(run.status, RunStatus.FAILED.value)


if __name__ == "__main__":
    unittest.main()
