"""Tests for run comparison (autoprompt_runner.compare)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import compare, storage  # noqa: E402
from autoprompt_runner.artifacts import ArtifactType  # noqa: E402


class CompareTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

        # Run A: a failing mock run that changed src/app.py + src/preview.py.
        self.run_a = storage.create_run(
            self.db, root_prompt="Fix placement preview tests", provider="mock",
            max_loops=2, require_approval=False,
        )
        step_a = storage.create_step(
            self.db, self.run_a, 0, "run tests", "FAILED",
            stderr="Traceback boom", exit_code=1, next_prompt="Fix the failing tests next",
        )
        storage.create_artifact(
            self.db, self.run_a, ArtifactType.CHANGED_FILES.value,
            content="src/app.py\nsrc/preview.py", step_id=step_a,
        )
        storage.create_artifact(
            self.db, self.run_a, ArtifactType.GIT_DIFF_STAT.value,
            content="2 files changed, 10 insertions(+)", step_id=step_a,
        )
        storage.create_artifact(
            self.db, self.run_a, ArtifactType.RUNNER_STDOUT.value, content="ran 3 tests", step_id=step_a
        )

        # Run B: a passing codex run that changed src/app.py + README.md.
        self.run_b = storage.create_run(
            self.db, root_prompt="Update the docs", provider="codex", max_loops=1, require_approval=False
        )
        step_b = storage.create_step(
            self.db, self.run_b, 0, "edit docs", "DONE", exit_code=0, next_prompt="Review the docs diff next"
        )
        storage.create_artifact(
            self.db, self.run_b, ArtifactType.CHANGED_FILES.value,
            content="src/app.py\nREADME.md", step_id=step_b,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_compare_two_runs(self):
        c = compare.compare_runs(self.db, self.run_a, self.run_b)
        self.assertEqual(c.run_a.id, self.run_a)
        self.assertEqual(c.run_b.id, self.run_b)
        self.assertFalse(c.same_provider)
        self.assertEqual(c.steps.step_count_a, 1)
        self.assertEqual(c.steps.step_count_b, 1)
        self.assertIn("placement", c.run_a.root_prompt_preview.lower())

    def test_missing_run_raises_not_found(self):
        with self.assertRaises(compare.CompareError) as ctx:
            compare.compare_runs(self.db, self.run_a, 9999)
        self.assertEqual(ctx.exception.kind, "not_found")

    def test_same_run_rejected(self):
        with self.assertRaises(compare.CompareError) as ctx:
            compare.compare_runs(self.db, self.run_a, self.run_a)
        self.assertEqual(ctx.exception.kind, "same_run")

    def test_changed_files_only_and_common(self):
        c = compare.compare_runs(self.db, self.run_a, self.run_b)
        self.assertEqual(c.changed_files.only_a, ["src/preview.py"])
        self.assertEqual(c.changed_files.only_b, ["README.md"])
        self.assertEqual(c.changed_files.common, ["src/app.py"])
        self.assertIsNone(c.changed_files.warning)

    def test_missing_changed_files_artifact_does_not_fail(self):
        run_c = storage.create_run(
            self.db, root_prompt="No git here", provider="mock", max_loops=1, require_approval=False
        )
        storage.create_step(self.db, run_c, 0, "noop", "DONE", exit_code=0)
        c = compare.compare_runs(self.db, self.run_a, run_c)
        self.assertEqual(c.changed_files.only_a, ["src/app.py", "src/preview.py"])
        self.assertEqual(c.changed_files.only_b, [])
        self.assertEqual(c.changed_files.common, [])
        self.assertIsNotNone(c.changed_files.warning)
        self.assertIn("run B", c.changed_files.warning)

    def test_artifact_counts_by_type(self):
        c = compare.compare_runs(self.db, self.run_a, self.run_b)
        self.assertEqual(c.artifact_counts_by_type_a.counts.get("changed_files"), 1)
        self.assertEqual(c.artifact_counts_by_type_a.counts.get("git_diff_stat"), 1)
        self.assertEqual(c.artifact_counts_by_type_a.counts.get("runner_stdout"), 1)
        self.assertEqual(c.artifact_counts_by_type_b.counts.get("changed_files"), 1)

    def test_artifact_counts_skipped_when_not_requested(self):
        c = compare.compare_runs(self.db, self.run_a, self.run_b, show_artifacts=False)
        self.assertEqual(c.artifact_counts_by_type_a.counts, {})
        self.assertEqual(c.artifact_counts_by_type_b.counts, {})

    def test_failed_step_summary(self):
        c = compare.compare_runs(self.db, self.run_a, self.run_b)
        self.assertEqual(c.steps.failed_steps_a, 1)
        self.assertEqual(c.steps.failed_steps_b, 0)
        self.assertEqual(c.steps.exit_codes_a, [1])
        self.assertEqual(c.steps.exit_codes_b, [0])

    def test_diff_stat_text(self):
        c = compare.compare_runs(self.db, self.run_a, self.run_b)
        self.assertIn("2 files changed", c.diff_stat_a)
        self.assertEqual(c.diff_stat_b, "")

    def test_next_prompt_previews_and_full(self):
        c = compare.compare_runs(self.db, self.run_a, self.run_b)
        self.assertIn("Review the docs diff", c.latest_next_prompt_b)
        self.assertIsNone(c.latest_next_prompt_full_b)  # full text not requested
        self.assertIsNone(c.run_a.root_prompt)

        full = compare.compare_runs(self.db, self.run_a, self.run_b, show_prompts=True)
        self.assertEqual(full.latest_next_prompt_full_b, "Review the docs diff next")
        self.assertEqual(full.run_a.root_prompt, "Fix placement preview tests")

    def test_summary_is_deterministic(self):
        first = compare.compare_runs(self.db, self.run_a, self.run_b).summary
        second = compare.compare_runs(self.db, self.run_a, self.run_b).summary
        self.assertEqual(first, second)
        self.assertIn(f"Run #{self.run_a}", first)


if __name__ == "__main__":
    unittest.main()
