"""Tests for run events / SSE formatting (autoprompt_runner.events + storage)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import events, storage  # noqa: E402
from autoprompt_runner.runners import ClaudeCodeRunner  # noqa: E402


class _FakePopen:
    def __init__(self, stdout="", stderr="", returncode=0):
        self._stdout, self._stderr, self.returncode = stdout, stderr, returncode

    def communicate(self, timeout=None):
        return self._stdout, self._stderr

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode


class RunEventStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_and_list_events(self):
        events.create_event(self.db, 1, events.RUN_CREATED, message="created")
        events.create_event(self.db, 1, events.RUN_STARTED, message="started")
        items = storage.list_run_events(self.db, 1)
        self.assertEqual([e.type for e in items], [events.RUN_CREATED, events.RUN_STARTED])

    def test_list_events_after_id(self):
        first = events.create_event(self.db, 1, events.RUN_CREATED)
        events.create_event(self.db, 1, events.STDOUT, message="line")
        after = storage.list_run_events(self.db, 1, after_id=first.id)
        self.assertEqual([e.type for e in after], [events.STDOUT])

    def test_events_are_scoped_per_run(self):
        events.create_event(self.db, 1, events.RUN_CREATED)
        events.create_event(self.db, 2, events.RUN_CREATED)
        self.assertEqual(len(storage.list_run_events(self.db, 1)), 1)

    def test_get_latest_run_event(self):
        events.create_event(self.db, 1, events.RUN_CREATED)
        last = events.create_event(self.db, 1, events.RUN_DONE)
        self.assertEqual(storage.get_latest_run_event(self.db, 1).id, last.id)

    def test_delete_old_run_events(self):
        for _ in range(5):
            events.create_event(self.db, 1, events.STDOUT, message="x")
        removed = storage.delete_old_run_events(self.db, 1, keep_last=2)
        self.assertEqual(removed, 3)
        self.assertEqual(len(storage.list_run_events(self.db, 1)), 2)


class EventSerializationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_serialize_and_parse_payload(self):
        event = events.create_event(self.db, 1, events.STDOUT, message="hello", payload={"k": "v"})
        data = events.serialize_event(event)
        self.assertEqual(data["type"], events.STDOUT)
        self.assertEqual(data["message"], "hello")
        self.assertEqual(data["payload"], {"k": "v"})
        self.assertEqual(events.parse_event_payload(event), {"k": "v"})

    def test_format_sse_event(self):
        event = events.create_event(self.db, 7, events.STDOUT, message="m")
        text = events.format_sse_event(event)
        self.assertTrue(text.startswith(f"id: {event.id}\n"))
        self.assertIn(f"event: {events.STDOUT}\n", text)
        self.assertIn("data: ", text)
        self.assertTrue(text.endswith("\n\n"))
        # The data line is valid JSON carrying the run id.
        data_line = [ln for ln in text.splitlines() if ln.startswith("data: ")][0][len("data: "):]
        self.assertEqual(json.loads(data_line)["run_id"], 7)


class RunnerOutputEventTests(unittest.TestCase):
    """The subprocess runner's set_output_callback turns captured output into line events."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_set_output_callback_emits_lines(self):
        captured = []
        runner = ClaudeCodeRunner(command="claude", workspace=self.ws)
        runner.set_output_callback(lambda stream, line: captured.append((stream, line)))
        with mock.patch(
            "autoprompt_runner.runners.claude_code.subprocess.Popen",
            return_value=_FakePopen(stdout="line1\nline2", stderr="err1", returncode=0),
        ):
            result = runner.run("hi")
        self.assertEqual(result.stdout, "line1\nline2")  # full output still captured
        self.assertIn(("stdout", "line1"), captured)
        self.assertIn(("stdout", "line2"), captured)
        self.assertIn(("stderr", "err1"), captured)

    def test_default_runner_does_not_require_callback(self):
        # Without a callback the runner behaves exactly as before (no streaming, no error).
        runner = ClaudeCodeRunner(command="claude", workspace=self.ws)
        with mock.patch(
            "autoprompt_runner.runners.claude_code.subprocess.Popen",
            return_value=_FakePopen(stdout="ok", returncode=0),
        ):
            self.assertEqual(runner.run("hi").stdout, "ok")


if __name__ == "__main__":
    unittest.main()
