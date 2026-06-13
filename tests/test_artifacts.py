"""Tests for artifact storage and the artifact capture helpers.

Standard-library only (unittest + tempfile + subprocess for a real git repo).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import artifacts, storage  # noqa: E402
from autoprompt_runner.artifacts import ArtifactType  # noqa: E402


class ArtifactStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.run_id = storage.create_run(
            self.db, root_prompt="p", provider="mock", max_loops=1, require_approval=False
        )
        self.step_id = storage.create_step(
            self.db, run_id=self.run_id, loop_index=0, prompt="p", status="DONE"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_and_get(self):
        artifact_id = storage.create_artifact(
            self.db, run_id=self.run_id, artifact_type="git_diff", content="diff text", step_id=self.step_id
        )
        artifact = storage.get_artifact(self.db, artifact_id)
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.type, "git_diff")
        self.assertEqual(artifact.content, "diff text")
        self.assertEqual(artifact.run_id, self.run_id)
        self.assertEqual(artifact.step_id, self.step_id)

    def test_get_missing_returns_none(self):
        self.assertIsNone(storage.get_artifact(self.db, 999))

    def test_list_for_run_and_type_filter(self):
        storage.create_artifact(self.db, self.run_id, "runner_stdout", content="out", step_id=self.step_id)
        storage.create_artifact(self.db, self.run_id, "runner_stderr", content="err", step_id=self.step_id)
        self.assertEqual(len(storage.list_artifacts_for_run(self.db, self.run_id)), 2)
        only_stdout = storage.list_artifacts_for_run(self.db, self.run_id, artifact_type="runner_stdout")
        self.assertEqual(len(only_stdout), 1)
        self.assertEqual(only_stdout[0].type, "runner_stdout")

    def test_list_for_step(self):
        storage.create_artifact(self.db, self.run_id, "changed_files", content="a.txt", step_id=self.step_id)
        types = [a.type for a in storage.list_artifacts_for_step(self.db, self.step_id)]
        self.assertIn("changed_files", types)

    def test_create_artifact_without_step(self):
        artifact_id = storage.create_artifact(self.db, run_id=self.run_id, artifact_type="git_skipped", content="skip")
        self.assertIsNone(storage.get_artifact(self.db, artifact_id).step_id)


class ArtifactHelperTests(unittest.TestCase):
    def test_artifact_type_values(self):
        self.assertEqual(ArtifactType.GIT_DIFF.value, "git_diff")
        self.assertEqual(ArtifactType.RUNNER_STDOUT.value, "runner_stdout")
        self.assertEqual(ArtifactType.GIT_SKIPPED.value, "git_skipped")

    def test_runner_output_artifacts(self):
        payloads = artifacts.runner_output_artifacts("out", "err")
        self.assertEqual([p.type for p in payloads], ["runner_stdout", "runner_stderr"])
        self.assertEqual(payloads[0].content, "out")
        self.assertEqual(payloads[1].content, "err")

    def test_git_skipped_artifact(self):
        payload = artifacts.git_skipped_artifact("reason")
        self.assertEqual(payload.type, "git_skipped")
        self.assertEqual(payload.content, "reason")

    def test_workspace_is_git_false_for_empty(self):
        self.assertFalse(artifacts.workspace_is_git(None))
        self.assertFalse(artifacts.workspace_is_git(""))

    def test_collect_post_step_git_artifacts_in_repo(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = tmp.name
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=test", "init", "-q"],
            cwd=repo, capture_output=True, text=True,
        )
        with open(os.path.join(repo, "f.txt"), "w", encoding="utf-8") as handle:
            handle.write("x")
        status_after = artifacts.capture_git_status(repo)
        payloads = artifacts.collect_post_step_git_artifacts(repo, "before", status_after)
        self.assertEqual(
            [p.type for p in payloads],
            ["git_status_before", "git_status_after", "git_diff", "git_diff_stat", "changed_files"],
        )
        changed = next(p for p in payloads if p.type == "changed_files")
        self.assertIn("f.txt", changed.content)


if __name__ == "__main__":
    unittest.main()
