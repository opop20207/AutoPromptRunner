"""Tests for the AutoPromptRunner CLI skeleton.

These tests use only the Python standard library (``unittest``) so they run without
installing anything:

    python -m unittest discover -s tests -v

They are also collected by ``pytest`` when it is available (``pythonpath = ["src"]``
is configured in pyproject.toml).
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

# Make the src-layout package importable without installing it.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import __version__  # noqa: E402
from autoprompt_runner.cli import main  # noqa: E402


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


class RunCommandTests(unittest.TestCase):
    def test_empty_prompt_is_rejected(self):
        code, out, err = run_cli(["run", "--prompt", "   ", "--provider", "mock"])
        self.assertNotEqual(code, 0)
        self.assertIn("prompt", err.lower())

    def test_invalid_max_loops_is_rejected(self):
        code, out, err = run_cli(["run", "--prompt", "hello", "--max-loops", "0"])
        self.assertNotEqual(code, 0)
        self.assertIn("max-loops", err.lower())

    def test_mock_run_succeeds(self):
        code, out, err = run_cli(
            ["run", "--prompt", "Improve README", "--provider", "mock", "--max-loops", "1"]
        )
        self.assertEqual(code, 0)
        self.assertIn("DONE", out)
        self.assertIn("mock", out.lower())
        self.assertIn("Improve README", out)


if __name__ == "__main__":
    unittest.main()
