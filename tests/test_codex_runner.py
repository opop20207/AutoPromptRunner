"""Tests for CodexRunner.

These tests never require a real ``codex`` executable: command-not-found is exercised
with a name that does not exist, and the success/timeout paths patch ``subprocess.Popen``
with a fake process. Standard-library only (unittest + tempfile + unittest.mock).
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
from autoprompt_runner.runners import CodexRunner  # noqa: E402

_PATCH_TARGET = "autoprompt_runner.runners.codex.subprocess.Popen"


class _FakePopen:
    """A minimal stand-in for subprocess.Popen used by the runner tests."""

    def __init__(self, stdout="", stderr="", returncode=0, timeout=False):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._timeout = timeout
        self._killed = False

    def communicate(self, timeout=None):
        if self._timeout and timeout is not None and not self._killed:
            raise subprocess.TimeoutExpired(cmd="codex", timeout=timeout)
        return self._stdout, self._stderr

    def kill(self):
        self._killed = True
        self.returncode = -9

    def terminate(self):
        self._killed = True

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode if self._killed else None


class CodexRunnerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_name_is_codex(self):
        self.assertEqual(CodexRunner(workspace=self.ws).name, "codex")

    def test_missing_workspace_raises_value_error(self):
        bad = os.path.join(self.ws, "does-not-exist")
        with self.assertRaises(ValueError):
            CodexRunner(workspace=bad)

    def test_none_workspace_is_allowed(self):
        self.assertEqual(CodexRunner(workspace=None).name, "codex")

    def test_command_not_found_returns_clean_result(self):
        runner = CodexRunner(command="definitely-not-a-real-cmd-xyz-123", workspace=self.ws)
        result = runner.run("hello")
        self.assertIsInstance(result, AgentResult)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not found", result.stderr.lower())
        self.assertTrue(result.started_at)
        self.assertTrue(result.finished_at)

    def test_successful_run_captures_output_and_invocation(self):
        with mock.patch(_PATCH_TARGET, return_value=_FakePopen(stdout="done", returncode=0)) as popen_mock:
            runner = CodexRunner(command="codex", timeout_seconds=42, workspace=self.ws)
            result = runner.run("hi there")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "done")
        args, kwargs = popen_mock.call_args
        self.assertEqual(args[0], ["codex", "exec", "hi there"])
        self.assertEqual(kwargs["cwd"], self.ws)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)
        self.assertTrue(kwargs["text"])
        self.assertFalse(kwargs.get("shell", False))

    def test_nonzero_exit_is_captured(self):
        with mock.patch(_PATCH_TARGET, return_value=_FakePopen(stderr="kaboom", returncode=3)):
            result = CodexRunner(command="codex", workspace=self.ws).run("x")
        self.assertEqual(result.exit_code, 3)
        self.assertIn("kaboom", result.stderr)

    def test_timeout_returns_clean_result(self):
        with mock.patch(_PATCH_TARGET, return_value=_FakePopen(timeout=True)):
            result = CodexRunner(command="codex", timeout_seconds=1, workspace=self.ws).run("hi")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("timed out", result.stderr.lower())
        self.assertTrue(result.started_at)
        self.assertTrue(result.finished_at)


if __name__ == "__main__":
    unittest.main()
