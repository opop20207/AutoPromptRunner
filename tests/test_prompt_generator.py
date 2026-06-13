"""Tests for the rule-based PromptGenerator.

Standard-library only (unittest). Runnable via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner.models import NextPrompt, PromptGenerationContext  # noqa: E402
from autoprompt_runner.services.prompt_generator import PromptGenerator  # noqa: E402


def make_context(**overrides):
    base = dict(
        root_prompt="Improve the signup endpoint",
        previous_prompt="prev prompt",
        exit_code=0,
        loop_index=0,
        max_loops=5,
        stdout="",
        stderr="",
        changed_files=[],
        git_diff_stat="",
        provider="mock",
        workspace=None,
    )
    base.update(overrides)
    return PromptGenerationContext(**base)


class PromptGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.gen = PromptGenerator()

    def test_success_with_changed_files(self):
        result = self.gen.generate(make_context(exit_code=0, changed_files=["a.py", "b.py"]))
        self.assertIsInstance(result, NextPrompt)
        self.assertEqual(result.kind, "continue")
        self.assertEqual(result.loop_index, 1)
        self.assertIn("Review the changed files", result.prompt)
        self.assertIn("a.py", result.prompt)
        self.assertIn("do not expand scope", result.prompt.lower())

    def test_success_with_no_changed_files(self):
        result = self.gen.generate(make_context(exit_code=0, changed_files=[]))
        self.assertEqual(result.kind, "no_changes")
        self.assertIn("no files", result.prompt.lower())
        self.assertIn("already complete", result.prompt.lower())

    def test_failure_with_stderr(self):
        result = self.gen.generate(make_context(exit_code=1, stderr="ValueError: bad input value"))
        self.assertEqual(result.kind, "fix")
        self.assertIn("stderr", result.prompt.lower())
        self.assertIn("bad input value", result.prompt)

    def test_failure_without_stderr(self):
        result = self.gen.generate(make_context(exit_code=1, stderr="", stdout="some plain output"))
        self.assertEqual(result.kind, "diagnose")
        self.assertIn("diagnose", result.prompt.lower())

    def test_max_loop_wrapup(self):
        result = self.gen.generate(make_context(exit_code=0, loop_index=4, max_loops=5, changed_files=["a.py"]))
        self.assertEqual(result.kind, "wrapup")
        self.assertIn("final", result.prompt.lower())
        self.assertIn("summarize", result.prompt.lower())

    def test_test_failure_detection_on_failure(self):
        result = self.gen.generate(
            make_context(exit_code=1, stderr="E   AssertionError: 1 != 2\nFAILED tests/test_x.py::test_y")
        )
        self.assertEqual(result.kind, "fix_tests")
        self.assertIn("failing tests", result.prompt.lower())

    def test_test_failure_detection_on_success(self):
        result = self.gen.generate(make_context(exit_code=0, stdout="2 passed, 1 failed", changed_files=["x.py"]))
        self.assertEqual(result.kind, "fix_tests")

    def test_large_changed_files(self):
        files = [f"file_{i}.py" for i in range(8)]
        result = self.gen.generate(make_context(exit_code=0, changed_files=files, git_diff_stat="8 files changed"))
        self.assertEqual(result.kind, "review_broad")
        self.assertIn("many files", result.prompt.lower())

    def test_prompts_stay_compact(self):
        long_text = "lorem ipsum " * 200
        contexts = [
            make_context(exit_code=0, changed_files=["a.py", "b.py"], root_prompt=long_text, stdout=long_text),
            make_context(exit_code=1, stderr=long_text, root_prompt=long_text),
            make_context(exit_code=1, stderr="", stdout=long_text, root_prompt=long_text),
            make_context(exit_code=0, changed_files=[], root_prompt=long_text),
            make_context(exit_code=0, loop_index=4, max_loops=5, root_prompt=long_text),
            make_context(exit_code=0, changed_files=[f"f{i}.py" for i in range(20)], git_diff_stat=long_text),
        ]
        for context in contexts:
            self.assertLessEqual(len(self.gen.generate(context).prompt), 600)

    def test_deterministic(self):
        context = make_context(exit_code=0, changed_files=["a.py"])
        self.assertEqual(self.gen.generate(context), self.gen.generate(context))

    def test_does_not_invent_code_fences(self):
        result = self.gen.generate(make_context(exit_code=0, changed_files=["real.py"]))
        self.assertNotIn("```", result.prompt)


if __name__ == "__main__":
    unittest.main()
