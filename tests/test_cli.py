"""Tests for the AutoPromptRunner CLI (version, init-db, run, list-runs, show-run).

Standard-library only (unittest + tempfile). Every command that touches the database
is given an explicit temporary ``--db-path`` so the tests never write into the working
tree. Runnable via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

# Make the src-layout package importable without installing it.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import __version__, storage  # noqa: E402
from autoprompt_runner.cli import main  # noqa: E402
from autoprompt_runner.state import RunStatus  # noqa: E402


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
        self.db = os.path.join(self._tmp.name, "autoprompt.db")

    def tearDown(self):
        self._tmp.cleanup()


class RunValidationTests(_DbTestCase):
    def test_empty_prompt_is_rejected(self):
        code, out, err = run_cli(["run", "--prompt", "   ", "--provider", "mock", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("prompt", err.lower())
        # Validation happens before the database is touched.
        self.assertFalse(os.path.exists(self.db))

    def test_invalid_max_loops_is_rejected(self):
        code, out, err = run_cli(["run", "--prompt", "hello", "--max-loops", "0", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("max-loops", err.lower())

    def test_unsupported_provider_is_rejected(self):
        code, out, err = run_cli(["run", "--prompt", "hello", "--provider", "claude_code", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("provider", err.lower())


class PersistenceTests(_DbTestCase):
    def test_init_db_command_creates_database(self):
        code, out, err = run_cli(["init-db", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.db))
        self.assertIn(self.db, out)

    def test_run_with_mock_creates_persisted_run(self):
        code, out, err = run_cli(
            ["run", "--prompt", "Improve README", "--provider", "mock", "--max-loops", "1", "--db-path", self.db]
        )
        self.assertEqual(code, 0)
        self.assertIn("DONE", out)
        self.assertIn("mock", out.lower())
        self.assertIn("Improve README", out)
        # Persisted: exactly one run in DONE with exactly one successful step.
        runs = storage.list_runs(self.db)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, RunStatus.DONE.value)
        self.assertEqual(runs[0].root_prompt, "Improve README")
        self.assertIsNotNone(runs[0].finished_at)
        steps = storage.get_steps_for_run(self.db, runs[0].id)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].exit_code, 0)

    def test_list_runs_shows_created_run(self):
        run_cli(["run", "--prompt", "first prompt", "--provider", "mock", "--db-path", self.db])
        code, out, err = run_cli(["list-runs", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("first prompt", out)
        self.assertIn("DONE", out)

    def test_list_runs_empty_database(self):
        code, out, err = run_cli(["list-runs", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("No runs found", out)

    def test_show_run_missing_id_exits_nonzero(self):
        run_cli(["init-db", "--db-path", self.db])
        code, out, err = run_cli(["show-run", "--id", "999", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())

    def test_show_run_displays_run_and_steps(self):
        run_cli(["run", "--prompt", "detail prompt", "--provider", "mock", "--db-path", self.db])
        run_id = storage.list_runs(self.db)[0].id
        code, out, err = run_cli(["show-run", "--id", str(run_id), "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn(f"Run #{run_id}", out)
        self.assertIn("detail prompt", out)
        self.assertIn("Steps", out)


if __name__ == "__main__":
    unittest.main()
