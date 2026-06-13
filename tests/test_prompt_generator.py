"""Tests for the deterministic PromptGenerator.

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

from autoprompt_runner.models import NextPrompt  # noqa: E402
from autoprompt_runner.services.prompt_generator import PromptGenerator  # noqa: E402


class PromptGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.gen = PromptGenerator()

    def test_success_next_prompt(self):
        result = self.gen.generate(
            root_prompt="Improve README",
            previous_prompt="Improve README",
            stdout="all good",
            stderr="",
            exit_code=0,
            loop_index=0,
        )
        self.assertIsInstance(result, NextPrompt)
        self.assertEqual(result.kind, "continue")
        self.assertEqual(result.loop_index, 1)
        self.assertIn("Continue", result.prompt)
        self.assertIn("report changed files", result.prompt)
        self.assertIn("Improve README", result.prompt)

    def test_failure_next_prompt(self):
        result = self.gen.generate(
            root_prompt="Improve README",
            previous_prompt="previous",
            stdout="",
            stderr="Traceback: boom happened",
            exit_code=1,
            loop_index=2,
        )
        self.assertEqual(result.kind, "fix")
        self.assertEqual(result.loop_index, 3)
        self.assertIn("Fix the failure", result.prompt)
        self.assertIn("stderr", result.prompt)
        self.assertIn("boom happened", result.prompt)

    def test_generation_is_deterministic(self):
        a = self.gen.generate("root", "prev", "out", "err", 0, 0)
        b = self.gen.generate("root", "prev", "out", "err", 0, 0)
        self.assertEqual(a, b)

    def test_does_not_invent_file_changes(self):
        # A success prompt should not fabricate concrete file edits.
        result = self.gen.generate("root task", "prev", "ok", "", 0, 0)
        self.assertNotIn("```", result.prompt)


if __name__ == "__main__":
    unittest.main()
