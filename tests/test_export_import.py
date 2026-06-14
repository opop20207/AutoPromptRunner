"""Tests for export / import (autoprompt_runner.export_import)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import export_import as ei  # noqa: E402
from autoprompt_runner import storage  # noqa: E402
from autoprompt_runner.artifacts import ArtifactType  # noqa: E402
from autoprompt_runner.state import RunStatus  # noqa: E402


class _ExportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self._tmp.name, "src.db")
        self.dst = os.path.join(self._tmp.name, "dst.db")
        storage.init_db(self.src)
        storage.init_db(self.dst)

    def tearDown(self):
        self._tmp.cleanup()

    def _populate(self):
        pid = storage.create_project(
            self.src, name="Demo", repo_path="/r", default_provider="mock",
            default_max_loops=2, require_approval=True, timeout_seconds=1800,
        )
        storage.create_template(self.src, name="Cont", body="do {{goal}}", description="d", tags=["x"])
        storage.create_provider_profile(
            self.src, name="claude-fast", type="claude-code", command="claude", default_timeout_seconds=1200
        )
        run_id = storage.create_run(
            self.src, root_prompt="Fix it", provider="mock", max_loops=2, require_approval=False, project_id=pid
        )
        step_id = storage.create_step(
            self.src, run_id, 0, "run tests", "FAILED", stdout="out", stderr="boom", exit_code=1, next_prompt="fix"
        )
        storage.create_artifact(self.src, run_id, ArtifactType.CHANGED_FILES.value, content="src/app.py", step_id=step_id)
        storage.create_artifact(self.src, run_id, "secret_dump", content="SUPERSECRET", path=".env", step_id=step_id)
        storage.create_approval(self.src, run_id, step_id, "fix")
        storage.update_run_status(self.src, run_id, RunStatus.FAILED.value)
        return pid, run_id, step_id


class BuildExportTests(_ExportTestCase):
    def test_build_export_payload(self):
        self._populate()
        payload = ei.build_export_payload(self.src)
        self.assertEqual(payload["format"], ei.FORMAT)
        self.assertEqual(payload["version"], ei.VERSION)
        data = payload["data"]
        self.assertEqual(len(data["projects"]), 1)
        self.assertEqual(len(data["templates"]), 1)
        self.assertEqual(len(data["provider_profiles"]), 1)
        self.assertEqual(len(data["runs"]), 1)
        self.assertEqual(len(data["steps"]), 1)
        self.assertEqual(len(data["approvals"]), 1)
        self.assertEqual(len(data["artifacts"]), 2)

    def test_export_selected_run_ids(self):
        self._populate()
        other = storage.create_run(self.src, root_prompt="Other", provider="mock", max_loops=1, require_approval=False)
        payload = ei.build_export_payload(self.src, run_ids=[other])
        ids = [r["id"] for r in payload["data"]["runs"]]
        self.assertEqual(ids, [other])
        # Steps/artifacts of the non-selected run are excluded.
        self.assertEqual(payload["data"]["steps"], [])

    def test_export_without_artifact_content(self):
        self._populate()
        payload = ei.build_export_payload(self.src, artifact_content=False, redact_sensitive=False)
        self.assertTrue(payload["data"]["artifacts"])
        self.assertTrue(all(a["content"] is None for a in payload["data"]["artifacts"]))

    def test_redaction_of_secret_like_artifact(self):
        self._populate()
        payload = ei.build_export_payload(self.src, redact_sensitive=True)
        self.assertTrue(payload["redacted"])
        self.assertEqual(payload["redacted_artifacts"], 1)
        secret = next(a for a in payload["data"]["artifacts"] if a["path"] == ".env")
        self.assertEqual(secret["content"], ei.REDACTION_PLACEHOLDER)
        self.assertTrue(secret["redacted"])
        # A normal artifact keeps its content.
        normal = next(a for a in payload["data"]["artifacts"] if a["type"] == "changed_files")
        self.assertEqual(normal["content"], "src/app.py")

    def test_no_redaction_when_disabled(self):
        self._populate()
        payload = ei.build_export_payload(self.src, redact_sensitive=False)
        self.assertFalse(payload["redacted"])
        secret = next(a for a in payload["data"]["artifacts"] if a["path"] == ".env")
        self.assertEqual(secret["content"], "SUPERSECRET")

    def test_write_and_read_file_round_trip(self):
        self._populate()
        path = os.path.join(self._tmp.name, "out.json")
        payload = ei.build_export_payload(self.src)
        ei.write_export_file(path, payload)
        self.assertEqual(ei.read_export_file(path)["format"], ei.FORMAT)


class ValidateTests(_ExportTestCase):
    def test_validate_valid_payload(self):
        payload = ei.build_export_payload(self.src)
        self.assertTrue(ei.validate_export_payload(payload))

    def test_reject_invalid_payload(self):
        with self.assertRaises(ei.ExportImportError):
            ei.validate_export_payload({"format": "nope", "version": 1, "data": {}})
        with self.assertRaises(ei.ExportImportError):
            ei.validate_export_payload({"format": ei.FORMAT, "version": 1})  # missing data

    def test_reject_unknown_major_version(self):
        with self.assertRaises(ei.ExportImportError) as ctx:
            ei.validate_export_payload({"format": ei.FORMAT, "version": 99, "data": {}})
        self.assertEqual(ctx.exception.kind, "invalid")

    def test_read_invalid_json(self):
        path = os.path.join(self._tmp.name, "bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("not json")
        with self.assertRaises(ei.ExportImportError):
            ei.read_export_file(path)


class ImportTests(_ExportTestCase):
    def test_import_merge_preserves_relationships(self):
        pid, run_id, step_id = self._populate()
        payload = ei.build_export_payload(self.src)
        result = ei.import_export_payload(self.dst, payload, mode="merge")
        self.assertGreater(result["imported"], 0)

        runs = storage.list_runs(self.dst)
        self.assertEqual(len(runs), 1)
        new_run = runs[0]
        self.assertEqual(new_run.status, RunStatus.FAILED.value)
        self.assertIsNotNone(new_run.project_id)  # remapped to the imported project
        steps = storage.get_steps_for_run(self.dst, new_run.id)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].stderr, "boom")
        artifacts = storage.list_artifacts_for_run(self.dst, new_run.id)
        self.assertEqual(len(artifacts), 2)
        # Artifacts/approvals point at the remapped run and step.
        self.assertTrue(all(a.run_id == new_run.id for a in artifacts))
        self.assertTrue(all(a.step_id == steps[0].id for a in artifacts if a.step_id is not None))

    def test_import_skip_existing_avoids_duplicates(self):
        self._populate()
        payload = ei.build_export_payload(self.src)
        ei.import_export_payload(self.dst, payload, mode="merge")
        result = ei.import_export_payload(self.dst, payload, mode="skip_existing")
        # Nothing new is created on the second import.
        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(storage.list_runs(self.dst)), 1)
        self.assertEqual(len(storage.list_templates(self.dst)), 1)
        self.assertEqual(len(storage.list_provider_profiles(self.dst)), 1)

    def test_merge_does_not_overwrite_existing_template(self):
        self._populate()
        # Pre-create a template of the same name with different body in the destination.
        storage.create_template(self.dst, name="Cont", body="ORIGINAL", tags=[])
        payload = ei.build_export_payload(self.src)
        ei.import_export_payload(self.dst, payload, mode="merge")
        self.assertEqual(storage.get_template_by_name(self.dst, "Cont").body, "ORIGINAL")  # unchanged

    def test_replace_templates_only_overwrites_template(self):
        self._populate()
        storage.create_template(self.dst, name="Cont", body="ORIGINAL", tags=[])
        payload = ei.build_export_payload(self.src)
        ei.import_export_payload(self.dst, payload, mode="replace_templates_only")
        self.assertEqual(storage.get_template_by_name(self.dst, "Cont").body, "do {{goal}}")  # replaced

    def test_import_partial_payload(self):
        # A payload with only templates imports cleanly (no runs/steps).
        payload = {
            "format": ei.FORMAT,
            "version": ei.VERSION,
            "exported_at": "t",
            "source": {"app": "AutoPromptRunner", "schema_version": 1},
            "data": {"templates": [{"id": 1, "name": "Solo", "description": None, "body": "x", "tags": None,
                                    "created_at": "t", "updated_at": None}]},
        }
        result = ei.import_export_payload(self.dst, payload, mode="merge")
        self.assertEqual(result["entities"]["templates"]["imported"], 1)
        self.assertIsNotNone(storage.get_template_by_name(self.dst, "Solo"))

    def test_import_rejects_unknown_mode(self):
        payload = ei.build_export_payload(self.src)
        with self.assertRaises(ei.ExportImportError):
            ei.import_export_payload(self.dst, payload, mode="bogus")


if __name__ == "__main__":
    unittest.main()
