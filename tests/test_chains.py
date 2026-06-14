"""Tests for prompt chain history (autoprompt_runner.chains)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import chains, storage  # noqa: E402
from autoprompt_runner.artifacts import ArtifactType  # noqa: E402


class ChainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.run_id = storage.create_run(
            self.db, root_prompt="Build the feature", provider="mock", max_loops=3, require_approval=True
        )
        # Step 0: success, approved, with changed_files + git_diff artifacts.
        self.s0 = storage.create_step(
            self.db, self.run_id, 0, "do step 0", "DONE", exit_code=0,
            stdout="ran ok", next_prompt="continue to step 1",
        )
        storage.create_artifact(
            self.db, self.run_id, ArtifactType.CHANGED_FILES.value, content="src/a.py\nsrc/b.py", step_id=self.s0
        )
        storage.create_artifact(
            self.db, self.run_id, ArtifactType.GIT_DIFF.value, content="diff text", step_id=self.s0
        )
        storage.create_approval(self.db, self.run_id, self.s0, "continue to step 1", status="APPROVED")
        # Step 1: failure, pending approval, no artifacts.
        self.s1 = storage.create_step(
            self.db, self.run_id, 1, "do step 1", "FAILED", exit_code=1,
            stderr="Traceback boom", next_prompt="fix the failure",
        )
        storage.create_approval(self.db, self.run_id, self.s1, "fix the failure")  # PENDING

    def tearDown(self):
        self._tmp.cleanup()

    def test_build_chain_multiple_steps(self):
        chain = chains.build_prompt_chain(self.db, self.run_id)
        self.assertEqual(chain.run_id, self.run_id)
        self.assertEqual(chain.step_count, 2)
        self.assertEqual([n.loop_index for n in chain.chain_nodes], [0, 1])
        self.assertEqual(chain.chain_nodes[0].node_id, f"{self.run_id}:{self.s0}")
        self.assertIn("step 1", chain.chain_nodes[1].prompt_preview)

    def test_build_chain_missing_run(self):
        with self.assertRaises(chains.ChainError) as ctx:
            chains.build_prompt_chain(self.db, 9999)
        self.assertEqual(ctx.exception.kind, "not_found")

    def test_chain_includes_approval_statuses(self):
        chain = chains.build_prompt_chain(self.db, self.run_id)
        self.assertEqual(chain.chain_nodes[0].approval_status, "APPROVED")
        self.assertEqual(chain.chain_nodes[1].approval_status, "PENDING")
        self.assertEqual(chain.approval_count, 2)

    def test_chain_includes_artifact_counts_by_type(self):
        chain = chains.build_prompt_chain(self.db, self.run_id)
        self.assertEqual(chain.chain_nodes[0].artifact_counts_by_type.counts.get("changed_files"), 1)
        self.assertEqual(chain.chain_nodes[0].artifact_counts_by_type.counts.get("git_diff"), 1)
        self.assertEqual(chain.chain_nodes[0].changed_files_preview, ["src/a.py", "src/b.py"])
        self.assertEqual(chain.total_artifact_count, 2)

    def test_chain_handles_missing_artifacts(self):
        # Step 1 has no artifacts: empty counts/changed files, and creation does not fail.
        chain = chains.build_prompt_chain(self.db, self.run_id)
        self.assertEqual(chain.chain_nodes[1].artifact_counts_by_type.counts, {})
        self.assertEqual(chain.chain_nodes[1].changed_files_preview, [])

    def test_include_artifacts_false_skips_counts(self):
        chain = chains.build_prompt_chain(self.db, self.run_id, include_artifacts=False)
        self.assertEqual(chain.chain_nodes[0].artifact_counts_by_type.counts, {})
        self.assertEqual(chain.chain_nodes[0].changed_files_preview, [])
        # Total is still reported (cheap aggregate).
        self.assertEqual(chain.total_artifact_count, 2)

    def test_full_prompts_flag(self):
        preview = chains.build_prompt_chain(self.db, self.run_id)
        self.assertIsNone(preview.chain_nodes[0].prompt)
        full = chains.build_prompt_chain(self.db, self.run_id, full_prompts=True)
        self.assertEqual(full.chain_nodes[0].prompt, "do step 0")
        self.assertEqual(full.chain_nodes[0].next_prompt, "continue to step 1")

    def test_failed_node_filtering(self):
        chain = chains.build_prompt_chain(self.db, self.run_id)
        failed = chains.get_failed_chain_nodes(chain.chain_nodes)
        self.assertEqual([n.step_id for n in failed], [self.s1])
        self.assertEqual(chain.failed_step_count, 1)

    def test_errors_only_filters_nodes_but_keeps_counts(self):
        chain = chains.build_prompt_chain(self.db, self.run_id, errors_only=True)
        self.assertEqual([n.step_id for n in chain.chain_nodes], [self.s1])
        self.assertEqual(chain.step_count, 2)  # summary still reflects the full run

    def test_pending_approval_node_detection(self):
        chain = chains.build_prompt_chain(self.db, self.run_id)
        self.assertTrue(chain.pending_approval)
        node = chains.get_pending_approval_node(chain.chain_nodes)
        self.assertIsNotNone(node)
        self.assertEqual(node.step_id, self.s1)

    def test_latest_chain_node(self):
        chain = chains.build_prompt_chain(self.db, self.run_id)
        latest = chains.get_latest_chain_node(chain.chain_nodes)
        self.assertEqual(latest.step_id, self.s1)
        self.assertIsNone(chains.get_latest_chain_node([]))


if __name__ == "__main__":
    unittest.main()
