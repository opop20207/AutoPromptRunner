"""Tests for the deterministic safety checks. Standard-library only."""

from __future__ import annotations

import os
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import config, safety  # noqa: E402


class SafetyTests(unittest.TestCase):
    def test_validate_max_loops_within_limit(self):
        self.assertEqual(safety.validate_max_loops(5), 5)
        self.assertEqual(safety.validate_max_loops(config.MAX_LOOPS_HARD_LIMIT), config.MAX_LOOPS_HARD_LIMIT)

    def test_validate_max_loops_hard_limit_and_min(self):
        with self.assertRaises(ValueError):
            safety.validate_max_loops(config.MAX_LOOPS_HARD_LIMIT + 1)
        with self.assertRaises(ValueError):
            safety.validate_max_loops(0)

    def test_validate_timeout_hard_limit_and_min(self):
        self.assertEqual(safety.validate_timeout_seconds(1800), 1800)
        with self.assertRaises(ValueError):
            safety.validate_timeout_seconds(config.TIMEOUT_SECONDS_HARD_LIMIT + 1)
        with self.assertRaises(ValueError):
            safety.validate_timeout_seconds(0)

    def test_scan_prompt_for_blocked_commands(self):
        self.assertIn("rm -rf /", safety.scan_prompt_for_blocked_commands("please run rm -rf / now"))
        self.assertIn("git push --force", safety.scan_prompt_for_blocked_commands("then git push --force origin"))
        self.assertEqual(safety.scan_prompt_for_blocked_commands("add a unit test and refactor"), [])

    def test_scan_prompt_no_false_positive_on_word_substring(self):
        self.assertEqual(safety.scan_prompt_for_blocked_commands("update the information section"), [])
        self.assertIn("format", safety.scan_prompt_for_blocked_commands("format the disk"))

    def test_scan_changed_files_for_secrets(self):
        files = ["src/app.py", ".env", "config/credentials.json", "deploy/id_rsa", "server.pem", "readme.md"]
        flagged = safety.scan_changed_files_for_secrets(files)
        self.assertIn(".env", flagged)
        self.assertIn("config/credentials.json", flagged)
        self.assertIn("deploy/id_rsa", flagged)
        self.assertIn("server.pem", flagged)
        self.assertNotIn("src/app.py", flagged)
        self.assertNotIn("readme.md", flagged)

    def test_detect_large_diff_by_file_count(self):
        files = [f"f{i}.py" for i in range(config.LARGE_CHANGED_FILES_THRESHOLD + 1)]
        self.assertIsNotNone(safety.detect_large_diff("", files))
        self.assertIsNone(safety.detect_large_diff("", ["a.py", "b.py"]))

    def test_detect_large_diff_by_line_count(self):
        big = " 2 files changed, 900 insertions(+), 200 deletions(-)"  # 1100 > 1000
        self.assertIsNotNone(safety.detect_large_diff(big, ["a.py", "b.py"]))
        small = " 1 file changed, 3 insertions(+)"
        self.assertIsNone(safety.detect_large_diff(small, ["a.py"]))

    def test_detect_risky_run(self):
        self.assertIsNotNone(safety.detect_risky_run("p", [".env"], ""))
        self.assertIsNone(safety.detect_risky_run("p", ["src/app.py"], " 1 file changed, 2 insertions(+)"))

    def test_build_safety_warnings(self):
        warnings = safety.build_safety_warnings(changed_files=[".env", "a.py"], diff_stat="")
        self.assertTrue(any("secret" in w for w in warnings))
        self.assertEqual(safety.build_safety_warnings(changed_files=["a.py"], diff_stat=""), [])

    def test_validate_workspace_allowed_no_allowlist(self):
        self.assertEqual(safety.validate_workspace_allowed("/tmp/anything", allowed_roots=[]), "/tmp/anything")
        self.assertIsNone(safety.validate_workspace_allowed(None, allowed_roots=["/tmp"]))

    def test_validate_workspace_allowed_within_root(self):
        root = os.path.abspath("..")
        workspace = os.path.abspath(".")
        self.assertEqual(safety.validate_workspace_allowed(workspace, allowed_roots=[root]), workspace)

    def test_validate_workspace_allowed_outside_root(self):
        outside_root = os.path.join(os.path.abspath("."), "definitely-not-a-parent-zzz")
        with self.assertRaises(ValueError):
            safety.validate_workspace_allowed(os.path.abspath("."), allowed_roots=[outside_root])


if __name__ == "__main__":
    unittest.main()
