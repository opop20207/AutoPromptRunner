"""Tests for the prompt-queue API (FastAPI TestClient + temp DB; injection stubbed)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_AUTH_ENV = ("AUTOPROMPT_AUTH_ENABLED", "AUTOPROMPT_API_TOKEN", "AUTOPROMPT_ALLOW_UNAUTHENTICATED_HEALTH")

try:
    from fastapi.testclient import TestClient

    from autoprompt_runner import app_injection, app_targets, storage
    from autoprompt_runner.api.app import app
    from autoprompt_runner.api.dependencies import get_db_path

    _HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    _HAVE_FASTAPI = False


def _clear_auth_env():
    for name in _AUTH_ENV:
        os.environ.pop(name, None)


def _stub_injector(prompt, submit_mode="paste_only", restore_clipboard_after=False):
    return app_injection.InjectionResult(True, True, False, False, True, submit_mode, "pasted (stub)")


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed (pip install -e '.[api]')")
class PromptQueueApiTests(unittest.TestCase):
    def setUp(self):
        _clear_auth_env()
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.target = app_targets.create_target(self.db, name="FC", session_label="FactoryColony")
        # Stub the desktop injection so the API never touches a real clipboard/app.
        self._orig_inject = app_injection.inject_prompt_to_active_window
        app_injection.inject_prompt_to_active_window = _stub_injector
        app.dependency_overrides[get_db_path] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app_injection.inject_prompt_to_active_window = self._orig_inject
        app.dependency_overrides.clear()
        _clear_auth_env()
        self._tmp.cleanup()

    def _queue(self):
        return self.client.post("/prompt-queues", json={"name": "34-36", "app_target_id": self.target.id}).json()["id"]

    def _add(self, qid, title="P34"):
        return self.client.post(f"/prompt-queues/{qid}/prompts", json={"title": title, "prompt": f"do {title}"}).json()

    def test_create_and_summary(self):
        qid = self._queue()
        resp = self.client.get(f"/prompt-queues/{qid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["queue"]["status"], "DRAFT")
        self.assertEqual(resp.json()["target"]["id"], self.target.id)

    def test_missing_queue_404(self):
        self.assertEqual(self.client.get("/prompt-queues/9999").status_code, 404)

    def test_add_and_reorder(self):
        qid = self._queue()
        self._add(qid, "P34")
        p2 = self._add(qid, "P35")
        resp = self.client.post(f"/prompt-queues/prompts/{p2['id']}/reorder", json={"new_position": 1})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["position"], 1)

    def test_inject_requires_user_confirmed(self):
        qid = self._queue()
        self._add(qid, "P34")
        # Default target is manual_confirm: injecting without user_confirmed is a 400.
        self.assertEqual(self.client.post(f"/prompt-queues/{qid}/inject-current", json={}).status_code, 400)
        self.assertEqual(
            self.client.post(f"/prompt-queues/{qid}/inject-current", json={"user_confirmed": False}).status_code, 400
        )

    def test_dry_run_returns_safety_only(self):
        qid = self._queue()
        self._add(qid, "P34")
        resp = self.client.post(f"/prompt-queues/{qid}/inject-current", json={"dry_run": True})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["dry_run"])
        self.assertIn("requires_confirmation", body["safety"])
        # State unchanged: the prompt is still pending/injectable.
        self.assertNotEqual(self.client.get(f"/prompt-queues/{qid}").json()["prompts"][0]["status"], "WAITING_COMPLETION")

    def test_inject_complete_flow(self):
        qid = self._queue()
        self._add(qid, "P34")
        self._add(qid, "P35")
        inj = self.client.post(f"/prompt-queues/{qid}/inject-current", json={"user_confirmed": True})
        self.assertEqual(inj.status_code, 200)
        self.assertEqual(inj.json()["prompt"]["status"], "WAITING_COMPLETION")
        self.assertTrue(inj.json()["target_summary"])
        # Second inject rejected while one is waiting.
        self.assertEqual(
            self.client.post(f"/prompt-queues/{qid}/inject-current", json={"user_confirmed": True}).status_code, 409
        )
        # Complete advances to the next.
        done = self.client.post(f"/prompt-queues/{qid}/complete-current")
        self.assertEqual(done.status_code, 200)
        statuses = [p["status"] for p in done.json()["prompts"]]
        self.assertEqual(statuses, ["DONE", "READY_TO_INJECT"])

    def test_inject_rejected_when_target_disabled(self):
        qid = self._queue()
        self._add(qid, "P34")
        self.client.post(f"/app-targets/{self.target.id}/disable")
        self.assertEqual(
            self.client.post(f"/prompt-queues/{qid}/inject-current", json={"user_confirmed": True}).status_code, 409
        )

    def test_inject_mismatch_returns_409(self):
        from autoprompt_runner import window_detection
        target = app_targets.create_target(
            self.db, name="Hint", verification_mode="window_title_hint", expected_window_title="ExpectedXYZ",
        )
        qid = self.client.post("/prompt-queues", json={"name": "h", "app_target_id": target.id}).json()["id"]
        self.client.post(f"/prompt-queues/{qid}/prompts", json={"title": "P", "prompt": "do"})
        orig = window_detection.get_active_window_info
        window_detection.get_active_window_info = lambda: window_detection.WindowInfo("Other", "x", "x", 1, "win32", True)
        try:
            self.assertEqual(
                self.client.post(f"/prompt-queues/{qid}/inject-current", json={"user_confirmed": True}).status_code, 409
            )
            ok = self.client.post(
                f"/prompt-queues/{qid}/inject-current", json={"user_confirmed": True, "allow_target_mismatch": True}
            )
            self.assertEqual(ok.status_code, 200)
        finally:
            window_detection.get_active_window_info = orig

    def test_complete_without_waiting_409(self):
        qid = self._queue()
        self._add(qid, "P34")
        self.assertEqual(self.client.post(f"/prompt-queues/{qid}/complete-current").status_code, 409)

    def test_pause_blocks_inject_then_resume(self):
        qid = self._queue()
        self._add(qid, "P34")
        self.assertEqual(self.client.post(f"/prompt-queues/{qid}/pause").json()["queue"]["status"], "PAUSED")
        self.assertEqual(
            self.client.post(f"/prompt-queues/{qid}/inject-current", json={"user_confirmed": True}).status_code, 409
        )
        self.client.post(f"/prompt-queues/{qid}/resume")
        self.assertEqual(
            self.client.post(f"/prompt-queues/{qid}/inject-current", json={"user_confirmed": True}).status_code, 200
        )

    def test_cancel_cancels_pending(self):
        qid = self._queue()
        self._add(qid, "P34")
        resp = self.client.post(f"/prompt-queues/{qid}/cancel")
        self.assertEqual(resp.json()["queue"]["status"], "CANCELLED")
        self.assertTrue(all(p["status"] == "CANCELLED" for p in resp.json()["prompts"]))

    def test_skip_current(self):
        qid = self._queue()
        self._add(qid, "P34")
        self._add(qid, "P35")
        resp = self.client.post(f"/prompt-queues/{qid}/skip-current")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["prompts"][0]["status"], "SKIPPED")

    def test_auth_required_when_enabled(self):
        qid = self._queue()
        os.environ["AUTOPROMPT_AUTH_ENABLED"] = "true"
        os.environ["AUTOPROMPT_API_TOKEN"] = "secret-token"
        self.assertEqual(self.client.get(f"/prompt-queues/{qid}").status_code, 401)
        self.assertEqual(self.client.post(f"/prompt-queues/{qid}/inject-current", json={}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
