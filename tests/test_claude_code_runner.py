"""Tests for ClaudeCodeRunner.

These tests never require a real ``claude`` executable: command-not-found is exercised
with a name that does not exist, and the success/timeout paths patch
``subprocess.run``. Standard-library only (unittest + tempfile + unittest.mock).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner.models import AgentResult  # noqa: E402
from autoprompt_runner.runners import ClaudeCodeRunner  # noqa: E402

_PATCH_TARGET = "autoprompt_runner.runners.claude_code.subprocess.run"


class ClaudeCodeRunnerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_name_is_claude_code(self):
        self.assertEqual(ClaudeCodeRunner(workspace=self.ws).name, "claude-code")

    def test_missing_workspace_raises_value_error(self):
        bad = os.path.join(self.ws, "does-not-exist")
        with self.assertRaises(ValueError):
            ClaudeCodeRunner(workspace=bad)

    def test_none_workspace_is_allowed(self):
        # Construction with no workspace is permitted (the CLI enforces it instead).
        runner = ClaudeCodeRunner(workspace=None)
        self.assertEqual(runner.name, "claude-code")

    def test_command_not_found_returns_clean_result(self):
        runner = ClaudeCodeRunner(command="definitely-not-a-real-cmd-xyz-123", workspace=self.ws)
        result = runner.run("hello")
        self.assertIsInstance(result, AgentResult)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not found", result.stderr.lower())
        self.assertTrue(result.started_at)
        self.assertTrue(result.finished_at)

    def test_successful_run_captures_output_and_invocation(self):
        fake = subprocess.CompletedProcess(args=["claude", "-p", "hi there"], returncode=0, stdout="done", stderr="")
        with mock.patch(_PATCH_TARGET, return_value=fake) as run_mock:
            runner = ClaudeCodeRunner(command="claude", timeout_seconds=42, workspace=self.ws)
            result = runner.run("hi there")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "done")
        args, kwargs = run_mock.call_args
        self.assertEqual(args[0], ["claude", "-p", "hi there"])
        self.assertEqual(kwargs["cwd"], self.ws)
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["timeout"], 42)
        self.assertFalse(kwargs.get("shell", False))

    def test_nonzero_exit_is_captured(self):
        fake = subprocess.CompletedProcess(args=["claude", "-p", "x"], returncode=2, stdout="", stderr="boom")
        with mock.patch(_PATCH_TARGET, return_value=fake):
            result = ClaudeCodeRunner(command="claude", workspace=self.ws).run("x")
        self.assertEqual(result.exit_code, 2)
        self.assertIn("boom", result.stderr)

    def test_timeout_returns_clean_result(self):
        def _raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

        with mock.patch(_PATCH_TARGET, side_effect=_raise_timeout):
            result = ClaudeCodeRunner(command="claude", timeout_seconds=1, workspace=self.ws).run("hi")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("timed out", result.stderr.lower())
        self.assertTrue(result.started_at)
        self.assertTrue(result.finished_at)


if __name__ == "__main__":
    unittest.main()
