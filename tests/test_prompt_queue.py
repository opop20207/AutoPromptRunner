"""Tests for the Claude Code app prompt queue (autoprompt_runner.prompt_queue).

Injection is exercised with a stub injector, so no real desktop/app is needed. Stdlib only.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import app_injection, app_targets, prompt_queue, storage  # noqa: E402


def _stub_injector(prompt, submit_mode="paste_only", restore_clipboard_after=False):
    return app_injection.InjectionResult(
        clipboard_set=True, paste_sent=True, submit_sent=False, clipboard_restored=False,
        automation_available=True, submit_mode=submit_mode, message="pasted (stub)",
    )


class PromptQueueTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.target = app_targets.create_target(self.db, name="FC", session_label="FactoryColony")
        self.queue = prompt_queue.create_queue(self.db, name="34-36", app_target_id=self.target.id)

    def tearDown(self):
        self._tmp.cleanup()

    def _add(self, title, body="body"):
        return prompt_queue.add_prompt_to_queue(self.db, self.queue.id, prompt=f"{body} {title}", title=title)

    def _inject(self):
        return prompt_queue.inject_current_prompt(self.db, self.queue.id, injector=_stub_injector)


class CreateAddTests(PromptQueueTestCase):
    def test_create_bound_to_target(self):
        self.assertEqual(self.queue.app_target_id, self.target.id)
        self.assertEqual(self.queue.status, "DRAFT")

    def test_create_missing_target_rejected(self):
        with self.assertRaises(prompt_queue.PromptQueueError) as ctx:
            prompt_queue.create_queue(self.db, name="x", app_target_id=9999)
        self.assertEqual(ctx.exception.kind, "not_found")

    def test_add_prompts_ordered_by_position(self):
        self._add("Prompt#34")
        self._add("Prompt#35")
        self._add("Prompt#36")
        prompts = storage.list_queued_prompts(self.db, self.queue.id)
        self.assertEqual([p.position for p in prompts], [1, 2, 3])
        self.assertEqual([p.title for p in prompts], ["Prompt#34", "Prompt#35", "Prompt#36"])
        self.assertEqual(storage.get_prompt_queue(self.db, self.queue.id).status, "READY")  # DRAFT -> READY

    def test_add_empty_prompt_rejected(self):
        with self.assertRaises(prompt_queue.PromptQueueError) as ctx:
            prompt_queue.add_prompt_to_queue(self.db, self.queue.id, prompt="   ")
        self.assertEqual(ctx.exception.kind, "invalid")


class ReorderTests(PromptQueueTestCase):
    def test_reorder_pending(self):
        p1 = self._add("Prompt#34")
        self._add("Prompt#35")
        p3 = self._add("Prompt#36")
        prompt_queue.reorder_prompt(self.db, p3.id, 1)
        self.assertEqual([p.title for p in storage.list_queued_prompts(self.db, self.queue.id)],
                         ["Prompt#36", "Prompt#34", "Prompt#35"])
        self.assertEqual(p1.title, "Prompt#34")  # sanity

    def test_reorder_rejected_for_non_pending(self):
        self._add("Prompt#34")
        self._inject()  # first prompt -> WAITING_COMPLETION
        waiting = storage.get_current_prompt(self.db, self.queue.id)
        with self.assertRaises(prompt_queue.PromptQueueError) as ctx:
            prompt_queue.reorder_prompt(self.db, waiting.id, 1)
        self.assertEqual(ctx.exception.kind, "invalid_state")


class InjectionTests(PromptQueueTestCase):
    def test_inject_sets_waiting_completion(self):
        self._add("Prompt#34")
        outcome = self._inject()
        self.assertEqual(outcome.prompt.status, "WAITING_COMPLETION")
        self.assertEqual(outcome.summary.queue.status, "RUNNING")
        self.assertTrue(outcome.target_summary)
        self.assertIsNotNone(outcome.prompt.injected_at)
        self.assertIsNotNone(outcome.prompt.submitted_at)

    def test_inject_rejected_when_another_waiting(self):
        self._add("Prompt#34")
        self._add("Prompt#35")
        self._inject()
        with self.assertRaises(prompt_queue.PromptQueueError) as ctx:
            self._inject()
        self.assertEqual(ctx.exception.kind, "invalid_state")

    def test_inject_rejected_when_target_disabled(self):
        self._add("Prompt#34")
        app_targets.disable_target(self.db, self.target.id)
        with self.assertRaises(prompt_queue.PromptQueueError) as ctx:
            self._inject()
        self.assertEqual(ctx.exception.kind, "invalid_state")

    def test_inject_rejected_when_no_prompt(self):
        with self.assertRaises(prompt_queue.PromptQueueError) as ctx:
            self._inject()
        self.assertEqual(ctx.exception.kind, "invalid_state")


class CompletionTests(PromptQueueTestCase):
    def test_complete_marks_done_and_next_ready(self):
        self._add("Prompt#34")
        self._add("Prompt#35")
        self._inject()
        summary = prompt_queue.mark_current_complete(self.db, self.queue.id)
        prompts = storage.list_queued_prompts(self.db, self.queue.id)
        self.assertEqual(prompts[0].status, "DONE")
        self.assertEqual(prompts[1].status, "READY_TO_INJECT")
        self.assertEqual(summary.queue.status, "RUNNING")

    def test_complete_rejected_when_nothing_waiting(self):
        self._add("Prompt#34")
        with self.assertRaises(prompt_queue.PromptQueueError) as ctx:
            prompt_queue.mark_current_complete(self.db, self.queue.id)
        self.assertEqual(ctx.exception.kind, "invalid_state")

    def test_complete_last_prompt_finishes_queue(self):
        self._add("Prompt#34")
        self._inject()
        summary = prompt_queue.mark_current_complete(self.db, self.queue.id)
        self.assertEqual(summary.queue.status, "DONE")
        self.assertIsNotNone(summary.queue.finished_at)

    def test_skip_marks_skipped_and_next_ready(self):
        self._add("Prompt#34")
        self._add("Prompt#35")
        summary = prompt_queue.skip_current_prompt(self.db, self.queue.id)
        prompts = storage.list_queued_prompts(self.db, self.queue.id)
        self.assertEqual(prompts[0].status, "SKIPPED")
        self.assertEqual(prompts[1].status, "READY_TO_INJECT")
        self.assertIsNotNone(summary)


class PauseCancelTests(PromptQueueTestCase):
    def test_pause_blocks_injection(self):
        self._add("Prompt#34")
        prompt_queue.pause_queue(self.db, self.queue.id)
        with self.assertRaises(prompt_queue.PromptQueueError) as ctx:
            self._inject()
        self.assertEqual(ctx.exception.kind, "invalid_state")

    def test_resume_allows_injection(self):
        self._add("Prompt#34")
        prompt_queue.pause_queue(self.db, self.queue.id)
        prompt_queue.resume_queue(self.db, self.queue.id)
        self.assertEqual(self._inject().prompt.status, "WAITING_COMPLETION")

    def test_cancel_cancels_pending_prompts(self):
        self._add("Prompt#34")
        self._add("Prompt#35")
        summary = prompt_queue.cancel_queue(self.db, self.queue.id)
        self.assertEqual(summary.queue.status, "CANCELLED")
        self.assertTrue(all(p.status == "CANCELLED" for p in storage.list_queued_prompts(self.db, self.queue.id)))

    def test_cancel_then_inject_rejected(self):
        self._add("Prompt#34")
        prompt_queue.cancel_queue(self.db, self.queue.id)
        with self.assertRaises(prompt_queue.PromptQueueError) as ctx:
            self._inject()
        self.assertEqual(ctx.exception.kind, "invalid_state")


if __name__ == "__main__":
    unittest.main()
