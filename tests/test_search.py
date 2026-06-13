"""Tests for SQLite LIKE search across runs, steps, and artifacts (standard library only).

Runnable via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import search, storage  # noqa: E402


class SearchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.run1 = storage.create_run(
            self.db, root_prompt="Fix the failing PlacementPreview tests", provider="mock",
            max_loops=2, require_approval=True,
        )
        self.step1 = storage.create_step(
            self.db, self.run1, loop_index=0, prompt="run the tests", status="DONE",
            stdout="ran the PlacementPreview suite", stderr="Traceback (most recent call last):\nAssertionError: 400",
            next_prompt="fix the failure",
        )
        storage.create_artifact(
            self.db, self.run1, "runner_stderr",
            content="Traceback (most recent call last): AssertionError: boom", step_id=self.step1,
        )
        storage.create_artifact(
            self.db, self.run1, "changed_files", content="src/app.py\nsrc/placement_preview.py", step_id=self.step1,
        )
        self.run2 = storage.create_run(
            self.db, root_prompt="Update the documentation", provider="codex", max_loops=1, require_approval=False,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_search_runs_by_prompt(self):
        results = search.search_runs(self.db, query="placementpreview")  # case-insensitive
        ids = [r.id for r in results]
        self.assertIn(self.run1, ids)
        self.assertNotIn(self.run2, ids)
        self.assertIn("PlacementPreview", results[0].prompt_preview)

    def test_search_runs_by_status(self):
        results = search.search_runs(self.db, status="CREATED")
        self.assertEqual({r.id for r in results}, {self.run1, self.run2})

    def test_search_runs_by_provider(self):
        self.assertEqual([r.id for r in search.search_runs(self.db, provider="codex")], [self.run2])

    def test_search_steps_by_stderr(self):
        results = search.search_steps(self.db, query="Traceback")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].match_field, "stderr")
        self.assertIn("Traceback", results[0].match_preview)

    def test_search_artifacts_by_content(self):
        results = search.search_artifacts(self.db, query="AssertionError")
        self.assertTrue(any(a.type == "runner_stderr" for a in results))

    def test_search_artifacts_by_type(self):
        results = search.search_artifacts(self.db, artifact_type="changed_files")
        self.assertTrue(results)
        self.assertTrue(all(a.type == "changed_files" for a in results))

    def test_search_changed_files(self):
        results = search.search_changed_files(self.db, path_query="placement_preview")
        self.assertTrue(any("placement_preview.py" in r.path for r in results))

    def test_search_all_returns_grouped(self):
        result = search.search_all(self.db, query="PlacementPreview")
        self.assertTrue(any(r.id == self.run1 for r in result.runs))
        self.assertTrue(any(s.run_id == self.run1 for s in result.steps))

    def test_limit_hard_cap_and_default(self):
        self.assertEqual(search._clamp_limit(9999), 200)  # hard cap
        self.assertEqual(search._clamp_limit(0), 50)  # default when invalid
        self.assertEqual(search._clamp_limit("bad"), 50)

    def test_empty_query_returns_recent_without_crash(self):
        self.assertEqual({r.id for r in search.search_runs(self.db, query="")}, {self.run1, self.run2})
        self.assertTrue(search.search_artifacts(self.db, query=None))  # all artifacts, no crash

    def test_no_match_returns_empty(self):
        self.assertEqual(search.search_runs(self.db, query="zzz-no-such-text"), [])


if __name__ == "__main__":
    unittest.main()
