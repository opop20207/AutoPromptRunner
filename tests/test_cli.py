"""Tests for the AutoPromptRunner CLI: validation, runs, projects, and artifacts.

Standard-library only (unittest + tempfile + unittest.mock + subprocess). Every command
that touches the database is given an explicit temporary ``--db-path`` so the tests
never write into the working tree, and the claude-code subprocess is patched so no real
``claude`` executable is required. Runnable via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autoprompt_runner import __version__, checkpoints, locks, storage  # noqa: E402
from autoprompt_runner.cli import main  # noqa: E402
from autoprompt_runner.state import RunStatus  # noqa: E402

_SUBPROCESS_RUN = "autoprompt_runner.runners.claude_code.subprocess.Popen"


def run_cli(argv):
    """Invoke the CLI in-process, capturing exit code, stdout, and stderr."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


_GIT_ENV = ["-c", "user.email=t@example.com", "-c", "user.name=test"]


def _init_repo(path):
    """Create a Git repo with one commit so worktrees have a HEAD to branch from."""
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", *_GIT_ENV, "init", "-q"], cwd=path, capture_output=True, text=True)
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as handle:
        handle.write("seed\n")
    subprocess.run(["git", *_GIT_ENV, "add", "."], cwd=path, capture_output=True, text=True)
    subprocess.run(["git", *_GIT_ENV, "commit", "-q", "-m", "init"], cwd=path, capture_output=True, text=True)


class VersionCommandTests(unittest.TestCase):
    def test_version_command_succeeds(self):
        code, out, err = run_cli(["version"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), __version__)


class _DbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = self._tmp.name
        self.db = os.path.join(self._tmp.name, "autoprompt.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _latest_run_id(self):
        return storage.list_runs(self.db)[0].id


class RunValidationTests(_DbTestCase):
    def test_empty_prompt_is_rejected(self):
        code, out, err = run_cli(["run", "--prompt", "   ", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("prompt", err.lower())
        self.assertFalse(os.path.exists(self.db))  # rejected before touching the DB

    def test_invalid_max_loops_is_rejected(self):
        code, out, err = run_cli(["run", "--prompt", "hello", "--max-loops", "0", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("max-loops", err.lower())

    def test_unsupported_provider_is_rejected(self):
        code, out, err = run_cli(["run", "--prompt", "hello", "--provider", "codex", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("provider", err.lower())

    def test_claude_code_requires_workspace(self):
        code, out, err = run_cli(["run", "--prompt", "hello", "--provider", "claude-code", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("workspace", err.lower())


class MockProviderFlowTests(_DbTestCase):
    def test_run_default_creates_pending_approval(self):
        code, out, err = run_cli(["run", "--prompt", "Improve README", "--max-loops", "3", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("WAITING_APPROVAL", out)
        run = storage.get_run(self.db, self._latest_run_id())
        self.assertEqual(run.status, RunStatus.WAITING_APPROVAL.value)
        self.assertIsNotNone(storage.get_pending_approval(self.db, run.id))

    def test_run_no_approval_autoruns_to_done(self):
        code, out, err = run_cli(
            ["run", "--prompt", "p", "--max-loops", "3", "--no-approval", "--db-path", self.db]
        )
        self.assertEqual(code, 0)
        self.assertIn("DONE", out)
        self.assertEqual(len(storage.get_steps_for_run(self.db, self._latest_run_id())), 3)

    def test_approve_next_executes_step(self):
        run_cli(["run", "--prompt", "p", "--max-loops", "3", "--db-path", self.db])
        rid = self._latest_run_id()
        code, out, err = run_cli(["approve-next", "--run-id", str(rid), "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertEqual(len(storage.get_steps_for_run(self.db, rid)), 2)

    def test_reject_next_stops_run(self):
        run_cli(["run", "--prompt", "p", "--max-loops", "3", "--db-path", self.db])
        rid = self._latest_run_id()
        code, out, err = run_cli(["reject-next", "--run-id", str(rid), "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("STOPPED", out)
        self.assertEqual(storage.get_run(self.db, rid).status, RunStatus.STOPPED.value)

    def test_show_run_includes_approval_state(self):
        run_cli(["run", "--prompt", "Improve README", "--max-loops", "3", "--db-path", self.db])
        rid = self._latest_run_id()
        code, out, err = run_cli(["show-run", "--id", str(rid), "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("WAITING_APPROVAL", out)
        self.assertIn("Pending approval", out)

    def test_show_run_missing_id_exits_nonzero(self):
        run_cli(["init-db", "--db-path", self.db])
        code, out, err = run_cli(["show-run", "--id", "999", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())

    def test_show_next_prompt_prints_full_block(self):
        code, out, err = run_cli(
            ["run", "--prompt", "Improve the project", "--max-loops", "3", "--show-next-prompt", "--db-path", self.db]
        )
        self.assertEqual(code, 0)
        self.assertIn("Next prompt (full):", out)

    def test_without_show_next_prompt_omits_full_block(self):
        code, out, err = run_cli(["run", "--prompt", "Improve the project", "--max-loops", "3", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertNotIn("Next prompt (full):", out)
        self.assertIn("next_prompt :", out)  # compact preview line still present


class ClaudeCodeProviderTests(_DbTestCase):
    def test_run_claude_code_command_unavailable_stores_failed(self):
        with mock.patch(_SUBPROCESS_RUN, side_effect=FileNotFoundError()):
            code, out, err = run_cli(
                [
                    "run", "--prompt", "Review project", "--provider", "claude-code",
                    "--workspace", self.ws, "--max-loops", "1", "--db-path", self.db,
                ]
            )
        self.assertNotEqual(code, 0)
        run = storage.get_run(self.db, self._latest_run_id())
        self.assertEqual(run.status, RunStatus.FAILED.value)
        self.assertEqual(run.workspace, self.ws)
        steps = storage.get_steps_for_run(self.db, run.id)
        self.assertEqual(len(steps), 1)
        self.assertNotEqual(steps[0].exit_code, 0)


class ProjectCommandTests(_DbTestCase):
    def _add(self, name="P", provider="mock", max_loops=5, repo=None):
        return run_cli([
            "project", "add", "--name", name, "--repo-path", repo or self.ws,
            "--provider", provider, "--max-loops", str(max_loops), "--db-path", self.db,
        ])

    def test_project_add_and_show(self):
        code, out, err = self._add(name="FactoryColony", max_loops=5)
        self.assertEqual(code, 0)
        self.assertEqual(storage.get_project_by_name(self.db, "FactoryColony").default_max_loops, 5)
        code, out, err = run_cli(["project", "show", "--name", "FactoryColony", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("FactoryColony", out)

    def test_project_add_invalid_repo_path(self):
        bad = os.path.join(self.ws, "nope")
        code, out, err = run_cli(["project", "add", "--name", "X", "--repo-path", bad, "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("repo-path", err.lower())

    def test_project_delete_clears_default_and_keeps_files(self):
        marker = os.path.join(self.ws, "keep.txt")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("x")
        self._add(name="A")
        run_cli(["project", "set-default", "--name", "A", "--db-path", self.db])
        code, out, err = run_cli(["project", "delete", "--name", "A", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIsNone(storage.get_default_project(self.db))
        self.assertTrue(os.path.exists(marker))


class RunWithProjectTests(_DbTestCase):
    def test_run_uses_selected_project_defaults(self):
        run_cli([
            "project", "add", "--name", "P", "--repo-path", self.ws,
            "--provider", "mock", "--max-loops", "4", "--db-path", self.db,
        ])
        code, out, err = run_cli(["run", "--project", "P", "--prompt", "Continue", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertEqual(storage.get_run(self.db, self._latest_run_id()).max_loops, 4)

    def test_explicit_args_override_project(self):
        run_cli([
            "project", "add", "--name", "P", "--repo-path", self.ws,
            "--provider", "mock", "--max-loops", "4", "--db-path", self.db,
        ])
        code, out, err = run_cli(
            ["run", "--project", "P", "--prompt", "Continue", "--max-loops", "2", "--db-path", self.db]
        )
        self.assertEqual(code, 0)
        self.assertEqual(storage.get_run(self.db, self._latest_run_id()).max_loops, 2)

    def test_run_project_not_found_exits_nonzero(self):
        run_cli(["init-db", "--db-path", self.db])
        code, out, err = run_cli(["run", "--project", "missing", "--prompt", "x", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())


class ArtifactCliTests(_DbTestCase):
    def _mock_run(self, prompt="p"):
        run_cli(["run", "--prompt", prompt, "--provider", "mock", "--max-loops", "1", "--no-approval", "--db-path", self.db])
        return self._latest_run_id()

    def test_show_artifacts_lists_runner_and_skip(self):
        rid = self._mock_run()
        code, out, err = run_cli(["show-artifacts", "--run-id", str(rid), "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("runner_stdout", out)
        self.assertIn("git_skipped", out)  # no workspace -> git skipped

    def test_show_artifacts_type_filter(self):
        rid = self._mock_run()
        code, out, err = run_cli(["show-artifacts", "--run-id", str(rid), "--type", "runner_stdout", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("runner_stdout", out)
        self.assertNotIn("runner_stderr", out)

    def test_show_artifact_prints_content(self):
        rid = self._mock_run()
        target = next(a for a in storage.list_artifacts_for_run(self.db, rid) if a.type == "runner_stdout")
        code, out, err = run_cli(["show-artifact", "--id", str(target.id), "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("mock", out.lower())

    def test_show_artifact_missing_id_exits_nonzero(self):
        run_cli(["init-db", "--db-path", self.db])
        code, out, err = run_cli(["show-artifact", "--id", "999", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())

    def test_git_workspace_run_captures_git_artifacts(self):
        repo = os.path.join(self.ws, "repo")
        os.makedirs(repo)
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=test", "init", "-q"],
            cwd=repo, capture_output=True, text=True,
        )
        run_cli([
            "run", "--prompt", "p", "--provider", "mock", "--workspace", repo,
            "--max-loops", "1", "--no-approval", "--db-path", self.db,
        ])
        rid = self._latest_run_id()
        code, out, err = run_cli(["show-artifacts", "--run-id", str(rid), "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("git_status_before", out)
        self.assertIn("git_diff", out)


class CodexProviderTests(_DbTestCase):
    _CODEX_RUN = "autoprompt_runner.runners.codex.subprocess.Popen"

    def test_run_codex_requires_workspace(self):
        code, out, err = run_cli(["run", "--prompt", "x", "--provider", "codex", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("workspace", err.lower())
        self.assertIn("codex", err.lower())  # recognized provider, not "unsupported"
        self.assertNotIn("unsupported", err.lower())

    def test_run_codex_command_unavailable_stores_failed(self):
        with mock.patch(self._CODEX_RUN, side_effect=FileNotFoundError()):
            code, out, err = run_cli(
                [
                    "run", "--prompt", "Review project", "--provider", "codex",
                    "--workspace", self.ws, "--max-loops", "1", "--db-path", self.db,
                ]
            )
        self.assertNotEqual(code, 0)
        run = storage.get_run(self.db, self._latest_run_id())
        self.assertEqual(run.status, RunStatus.FAILED.value)
        self.assertEqual(run.provider, "codex")
        self.assertEqual(run.workspace, self.ws)
        steps = storage.get_steps_for_run(self.db, run.id)
        self.assertEqual(len(steps), 1)
        self.assertNotEqual(steps[0].exit_code, 0)

    def test_project_add_accepts_codex(self):
        code, out, err = run_cli(
            ["project", "add", "--name", "CX", "--repo-path", self.ws, "--provider", "codex", "--db-path", self.db]
        )
        self.assertEqual(code, 0)
        self.assertEqual(storage.get_project_by_name(self.db, "CX").default_provider, "codex")

    def test_run_project_codex_uses_repo_path_workspace(self):
        run_cli([
            "project", "add", "--name", "CX", "--repo-path", self.ws,
            "--provider", "codex", "--max-loops", "1", "--db-path", self.db,
        ])
        with mock.patch(self._CODEX_RUN, side_effect=FileNotFoundError()):
            code, out, err = run_cli(["run", "--project", "CX", "--prompt", "Review", "--db-path", self.db])
        self.assertNotEqual(code, 0)  # codex unavailable -> FAILED
        run = storage.get_run(self.db, self._latest_run_id())
        self.assertEqual(run.provider, "codex")
        self.assertEqual(run.workspace, self.ws)  # workspace came from project repo_path
        self.assertEqual(run.status, RunStatus.FAILED.value)

    def test_claude_code_still_recognized(self):
        code, out, err = run_cli(["run", "--prompt", "x", "--provider", "claude-code", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("workspace", err.lower())
        self.assertNotIn("unsupported", err.lower())

    def test_mock_still_works_without_workspace(self):
        code, out, err = run_cli(
            ["run", "--prompt", "p", "--provider", "mock", "--max-loops", "1", "--no-approval", "--db-path", self.db]
        )
        self.assertEqual(code, 0)
        self.assertEqual(storage.get_run(self.db, self._latest_run_id()).status, RunStatus.DONE.value)


class SafetyCliTests(_DbTestCase):
    def test_safety_check_blocker_exits_nonzero(self):
        code, out, err = run_cli(["safety-check", "--prompt", "then run rm -rf / on the repo"])
        self.assertNotEqual(code, 0)
        self.assertIn("blocked command pattern", out)

    def test_safety_check_clean_exits_zero(self):
        code, out, err = run_cli(["safety-check", "--prompt", "improve the README and add tests"])
        self.assertEqual(code, 0)
        self.assertIn("none", out)

    def test_run_rejects_max_loops_above_hard_limit(self):
        code, out, err = run_cli(["run", "--prompt", "p", "--max-loops", "9999", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("hard limit", err.lower())

    def test_run_blocked_prompt_fails(self):
        code, out, err = run_cli(
            ["run", "--prompt", "please run rm -rf / now", "--max-loops", "1", "--db-path", self.db]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("blocked", err.lower())
        run = storage.get_run(self.db, self._latest_run_id())
        self.assertEqual(run.status, RunStatus.FAILED.value)
        self.assertTrue(storage.list_artifacts_for_run(self.db, run.id, artifact_type="safety_blocker"))


class TemplateCliTests(_DbTestCase):
    def test_seed_then_list_shows_builtin(self):
        code, out, err = run_cli(["template", "seed", "--db-path", self.db])
        self.assertEqual(code, 0)
        code, out, err = run_cli(["template", "list", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("Fix failing tests", out)

    def test_add_and_show(self):
        code, out, err = run_cli([
            "template", "add", "--name", "Small step",
            "--description", "next smallest task",
            "--body", "Implement the next smallest task for {{project_name}}. Goal: {{goal}}",
            "--db-path", self.db,
        ])
        self.assertEqual(code, 0)
        code, out, err = run_cli(["template", "show", "--name", "Small step", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("{{project_name}}", out)

    def test_add_duplicate_rejected(self):
        run_cli(["template", "add", "--name", "Dup", "--body", "x", "--db-path", self.db])
        code, out, err = run_cli(["template", "add", "--name", "Dup", "--body", "y", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("already exists", err.lower())

    def test_render_substitutes_goal(self):
        run_cli(["template", "seed", "--db-path", self.db])
        code, out, err = run_cli([
            "template", "render", "--name", "Fix failing tests",
            "--goal", "Fix the placement preview tests", "--db-path", self.db,
        ])
        self.assertEqual(code, 0)
        self.assertIn("Fix the placement preview tests", out)
        self.assertNotIn("{{goal}}", out)

    def test_delete_template(self):
        run_cli(["template", "add", "--name", "Temp", "--body", "x", "--db-path", self.db])
        code, out, err = run_cli(["template", "delete", "--name", "Temp", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIsNone(storage.get_template_by_name(self.db, "Temp"))

    def test_render_missing_template_exits_nonzero(self):
        run_cli(["init-db", "--db-path", self.db])
        code, out, err = run_cli(["template", "render", "--name", "nope", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())


class RunFromTemplateCliTests(_DbTestCase):
    def test_run_from_template_creates_run(self):
        run_cli(["template", "seed", "--db-path", self.db])
        code, out, err = run_cli([
            "run", "--template", "Fix failing tests",
            "--goal", "Fix failing placement preview tests",
            "--provider", "mock", "--max-loops", "1", "--no-approval", "--db-path", self.db,
        ])
        self.assertEqual(code, 0)
        self.assertIn("DONE", out)
        run = storage.get_run(self.db, self._latest_run_id())
        self.assertIn("Fix failing placement preview tests", run.root_prompt)

    def test_run_rejects_prompt_and_template_together(self):
        run_cli(["template", "seed", "--db-path", self.db])
        code, out, err = run_cli([
            "run", "--prompt", "p", "--template", "Fix failing tests", "--db-path", self.db,
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("both", err.lower())

    def test_run_missing_template_exits_nonzero(self):
        run_cli(["init-db", "--db-path", self.db])
        code, out, err = run_cli(["run", "--template", "nope", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())


class WorktreeCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        self.repo = os.path.join(self._tmp.name, "repo")
        _init_repo(self.repo)
        code, _out, err = run_cli([
            "project", "add", "--name", "P", "--repo-path", self.repo,
            "--provider", "mock", "--db-path", self.db,
        ])
        self.assertEqual(code, 0, err)

    def tearDown(self):
        self._tmp.cleanup()

    def _latest_run_id(self):
        return storage.list_runs(self.db)[0].id

    def _create(self, name="ui-session", branch=None):
        return run_cli([
            "worktree", "create", "--project", "P", "--name", name,
            "--branch", branch or f"autoprompt/{name}", "--db-path", self.db,
        ])

    def test_create_list_show(self):
        code, out, err = self._create()
        self.assertEqual(code, 0, err)
        wt = storage.get_worktree_by_name(self.db, "ui-session")
        self.assertIsNotNone(wt)
        self.assertTrue(os.path.isdir(wt.path))
        code, out, err = run_cli(["worktree", "list", "--project", "P", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("ui-session", out)
        code, out, err = run_cli(["worktree", "show", "--name", "ui-session", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("autoprompt/ui-session", out)

    def test_create_invalid_name_rejected(self):
        code, out, err = run_cli([
            "worktree", "create", "--project", "P", "--name", "bad/name",
            "--branch", "autoprompt/x", "--db-path", self.db,
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("name", err.lower())

    def test_archive_keeps_disk_files(self):
        self._create()
        wt = storage.get_worktree_by_name(self.db, "ui-session")
        code, out, err = run_cli(["worktree", "archive", "--name", "ui-session", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertEqual(storage.get_worktree_by_name(self.db, "ui-session").status, "ARCHIVED")
        self.assertTrue(os.path.isdir(wt.path))  # disk files kept

    def test_remove_deletes_record_and_dir(self):
        self._create()
        wt = storage.get_worktree_by_name(self.db, "ui-session")
        code, out, err = run_cli(["worktree", "remove", "--name", "ui-session", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIsNone(storage.get_worktree_by_name(self.db, "ui-session"))
        self.assertFalse(os.path.isdir(wt.path))

    def test_run_uses_worktree_path_as_workspace(self):
        self._create()
        wt = storage.get_worktree_by_name(self.db, "ui-session")
        code, out, err = run_cli([
            "run", "--project", "P", "--worktree", "ui-session", "--prompt", "p",
            "--provider", "mock", "--max-loops", "1", "--no-approval", "--db-path", self.db,
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(storage.get_run(self.db, self._latest_run_id()).workspace, wt.path)

    def test_explicit_workspace_overrides_worktree(self):
        self._create()
        code, out, err = run_cli([
            "run", "--project", "P", "--worktree", "ui-session", "--workspace", self.repo,
            "--prompt", "p", "--provider", "mock", "--max-loops", "1", "--no-approval", "--db-path", self.db,
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(storage.get_run(self.db, self._latest_run_id()).workspace, self.repo)

    def test_run_archived_worktree_rejected(self):
        self._create()
        run_cli(["worktree", "archive", "--name", "ui-session", "--db-path", self.db])
        code, out, err = run_cli([
            "run", "--project", "P", "--worktree", "ui-session", "--prompt", "p", "--db-path", self.db,
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("archived", err.lower())

    def test_run_missing_worktree_rejected(self):
        code, out, err = run_cli([
            "run", "--project", "P", "--worktree", "nope", "--prompt", "p", "--db-path", self.db,
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())


class LocksCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        self.ws = os.path.join(self._tmp.name, "ws")
        os.makedirs(self.ws)
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_locks_list_after_workspace_run(self):
        run_cli([
            "run", "--prompt", "p", "--provider", "mock", "--workspace", self.ws,
            "--max-loops", "1", "--no-approval", "--db-path", self.db,
        ])
        code, out, err = run_cli(["locks", "list", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("RELEASED", out)  # the run acquired then released a lock

    def test_locks_release(self):
        from datetime import datetime, timedelta, timezone

        storage.create_run_lock(
            self.db, workspace_path=locks.normalize_workspace(self.ws), run_id=42,
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
        code, out, err = run_cli(["locks", "release", "--run-id", "42", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("Released", out)
        self.assertEqual(storage.get_lock_for_run(self.db, 42).status, "RELEASED")

    def test_run_blocked_by_active_lock(self):
        locks.acquire_lock(self.db, self.ws, run_id=999, timeout_seconds=60)
        code, out, err = run_cli([
            "run", "--prompt", "p", "--provider", "mock", "--workspace", self.ws,
            "--max-loops", "1", "--no-approval", "--db-path", self.db,
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("locked", err.lower())


class QueueWorkerCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _latest_run_id(self):
        return storage.list_runs(self.db)[0].id

    def _queue(self, *extra):
        return run_cli([
            "run", "--prompt", "p", "--provider", "mock", "--max-loops", "1", "--queued",
            "--db-path", self.db, *extra,
        ])

    def test_run_queued_enqueues_without_executing(self):
        code, out, err = self._queue()
        self.assertEqual(code, 0)
        self.assertIn("Queued run", out)
        run_id = self._latest_run_id()
        self.assertEqual(storage.get_run(self.db, run_id).status, "CREATED")
        self.assertEqual(storage.get_job_by_run_id(self.db, run_id).status, "QUEUED")

    def test_queue_list(self):
        self._queue()
        code, out, err = run_cli(["queue", "list", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("QUEUED", out)

    def test_queue_cancel(self):
        self._queue()
        run_id = self._latest_run_id()
        code, out, err = run_cli(["queue", "cancel", "--run-id", str(run_id), "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("Cancelled", out)
        self.assertEqual(storage.get_job_by_run_id(self.db, run_id).status, "CANCELLED")

    def test_worker_run_once_executes_job(self):
        self._queue("--no-approval")
        run_id = self._latest_run_id()
        code, out, err = run_cli(["worker", "run", "--once", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("executed one job", out)
        self.assertEqual(storage.get_run(self.db, run_id).status, "DONE")

    def test_worker_run_once_empty_queue(self):
        code, out, err = run_cli(["worker", "run", "--once", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("no queued jobs", out)

    def test_run_cancel_queued_run(self):
        self._queue()
        run_id = self._latest_run_id()
        code, out, err = run_cli(["run", "cancel", "--run-id", str(run_id), "--reason", "stop", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("Cancelled", out)
        self.assertEqual(storage.get_run(self.db, run_id).status, "STOPPED")
        self.assertEqual(storage.get_job_by_run_id(self.db, run_id).status, "CANCELLED")
        self.assertEqual(storage.get_cancellation_for_run(self.db, run_id).status, "COMPLETED")

    def test_run_cancel_missing_run_exits_nonzero(self):
        code, out, err = run_cli(["run", "cancel", "--run-id", "9999", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())

    def test_queue_cancel_uses_cancellation_service(self):
        self._queue()
        run_id = self._latest_run_id()
        code, out, err = run_cli(["queue", "cancel", "--run-id", str(run_id), "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("Cancelled", out)
        self.assertEqual(storage.get_run(self.db, run_id).status, "STOPPED")


class SystemCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _stale_running_run(self):
        """A RUNNING run backdated so it is stale under the real present."""
        rid = storage.create_run(
            self.db, root_prompt="x", provider="mock", max_loops=1, require_approval=False, timeout_seconds=60
        )
        storage.update_run_status(self.db, rid, RunStatus.RUNNING.value)
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "UPDATE runs SET created_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", rid)
            )
            conn.commit()
        finally:
            conn.close()
        return rid

    def test_status_clean_db(self):
        code, out, err = run_cli(["system", "status", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("Workers:", out)
        self.assertIn("Queue:", out)
        self.assertIn("Locks:", out)
        self.assertIn("0 stale RUNNING", out)

    def test_status_reports_stale_run(self):
        self._stale_running_run()
        code, out, err = run_cli(["system", "status", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("1 stale RUNNING", out)

    def test_reconcile_dry_run_does_not_modify(self):
        rid = self._stale_running_run()
        code, out, err = run_cli(["system", "reconcile", "--dry-run", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("dry-run", out)
        self.assertIn("1 run(s)", out)
        self.assertEqual(storage.get_run(self.db, rid).status, RunStatus.RUNNING.value)  # unchanged

    def test_reconcile_apply_marks_stale_run_failed(self):
        rid = self._stale_running_run()
        code, out, err = run_cli(["system", "reconcile", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("applied", out)
        self.assertEqual(storage.get_run(self.db, rid).status, RunStatus.FAILED.value)

    def test_system_requires_subcommand(self):
        code, out, err = run_cli(["system"])
        self.assertNotEqual(code, 0)
        self.assertIn("subcommand", err.lower())


class CheckpointCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "autoprompt.db")
        storage.init_db(self.db)
        self.ws = os.path.join(self._tmp.name, "repo")
        _init_repo(self.ws)  # creates a git repo with README.md == "seed\n" committed
        self.run_id = storage.create_run(
            self.db, root_prompt="p", provider="mock", max_loops=1, require_approval=False, workspace=self.ws
        )
        self.cp = checkpoints.create_checkpoint(self.db, self.run_id, None, self.ws)

    def tearDown(self):
        self._tmp.cleanup()

    def _readme(self):
        with open(os.path.join(self.ws, "README.md"), encoding="utf-8") as handle:
            return handle.read()

    def _edit_readme(self):
        with open(os.path.join(self.ws, "README.md"), "w", encoding="utf-8") as handle:
            handle.write("CHANGED\n")

    def test_list(self):
        code, out, err = run_cli(["checkpoint", "list", "--run-id", str(self.run_id), "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("CREATED", out)

    def test_show_includes_rollback_plan(self):
        code, out, err = run_cli(["checkpoint", "show", "--id", str(self.cp.id), "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn(f"Checkpoint #{self.cp.id}", out)
        self.assertIn("Rollback plan", out)

    def test_rollback_plan_does_not_modify_files(self):
        self._edit_readme()
        code, out, err = run_cli(["checkpoint", "rollback-plan", "--id", str(self.cp.id), "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("git reset --hard", out)
        self.assertEqual(self._readme(), "CHANGED\n")  # the plan changed nothing

    def test_rollback_requires_confirm(self):
        code, out, err = run_cli(["checkpoint", "rollback", "--id", str(self.cp.id), "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("confirm", err.lower())

    def test_rollback_with_confirm_restores(self):
        self._edit_readme()
        code, out, err = run_cli(
            ["checkpoint", "rollback", "--id", str(self.cp.id), "--confirm", "--db-path", self.db]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("rolled back", out.lower())
        self.assertEqual(self._readme(), "seed\n")  # restored to the committed state
        self.assertEqual(checkpoints.get_checkpoint(self.db, self.cp.id).status, storage.CHECKPOINT_RESTORED)

    def test_requires_subcommand(self):
        code, out, err = run_cli(["checkpoint"])
        self.assertNotEqual(code, 0)
        self.assertIn("subcommand", err.lower())


class ConfigCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)  # clean cwd: no autoprompt.toml is found here
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("AUTOPROMPT_")}
        self._env = mock.patch.dict(os.environ, clean_env, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_config_show(self):
        code, out, err = run_cli(["config", "show"])
        self.assertEqual(code, 0, err)
        self.assertIn("[defaults]", out)
        self.assertIn("provider", out)
        self.assertIn("[safety]", out)

    def test_config_validate_ok(self):
        code, out, err = run_cli(["config", "validate"])
        self.assertEqual(code, 0, err)
        self.assertIn("valid", out.lower())

    def test_config_validate_invalid_exits_nonzero(self):
        cfg = os.path.join(self._tmp.name, "bad.toml")
        with open(cfg, "w", encoding="utf-8") as handle:
            handle.write("[defaults]\nmax_loops = 999\n\n[safety]\nmax_loops_hard_limit = 20\n")
        code, out, err = run_cli(["--config", cfg, "config", "validate"])
        self.assertNotEqual(code, 0)
        self.assertIn("invalid", err.lower())

    def test_config_init_creates_and_refuses_overwrite(self):
        code, out, err = run_cli(["config", "init"])
        self.assertEqual(code, 0, err)
        target = os.path.join(".autoprompt", "config.toml")
        self.assertTrue(os.path.exists(target))
        self.assertIn("Created", out)
        code2, out2, err2 = run_cli(["config", "init"])  # without --force -> refuse
        self.assertNotEqual(code2, 0)
        self.assertIn("already exists", err2.lower())
        code3, out3, err3 = run_cli(["config", "init", "--force"])  # --force -> overwrite
        self.assertEqual(code3, 0, err3)


class SearchCliTests(_DbTestCase):
    def setUp(self):
        super().setUp()
        storage.init_db(self.db)
        self.run1 = storage.create_run(
            self.db, root_prompt="Fix the failing tests", provider="mock", max_loops=1, require_approval=False
        )
        step = storage.create_step(self.db, self.run1, 0, "run tests", "DONE", stderr="Traceback boom")
        storage.create_artifact(
            self.db, self.run1, "runner_stderr", content="Traceback (most recent call last)", step_id=step
        )

    def test_search_runs(self):
        code, out, err = run_cli(["search", "runs", "--query", "failing", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn(str(self.run1), out)

    def test_search_runs_no_match(self):
        code, out, err = run_cli(["search", "runs", "--query", "zzz-no-such-text", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("No matching runs", out)

    def test_search_artifacts(self):
        code, out, err = run_cli(
            ["search", "artifacts", "--query", "Traceback", "--type", "runner_stderr", "--db-path", self.db]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("runner_stderr", out)

    def test_search_all(self):
        code, out, err = run_cli(["search", "all", "--query", "Traceback", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("Runs (", out)
        self.assertIn("Steps (", out)
        self.assertIn("Artifacts (", out)


class CompareCliTests(_DbTestCase):
    def setUp(self):
        super().setUp()
        storage.init_db(self.db)
        self.run_a = storage.create_run(
            self.db, root_prompt="Fix the failing tests", provider="mock", max_loops=1, require_approval=False
        )
        step_a = storage.create_step(
            self.db, self.run_a, 0, "run tests", "FAILED", exit_code=1, next_prompt="Fix the tests next"
        )
        storage.create_artifact(self.db, self.run_a, "changed_files", content="src/app.py", step_id=step_a)
        self.run_b = storage.create_run(
            self.db, root_prompt="Update docs", provider="codex", max_loops=1, require_approval=False
        )
        storage.create_step(self.db, self.run_b, 0, "edit docs", "DONE", exit_code=0)

    def test_compare_runs(self):
        code, out, err = run_cli(
            ["compare", "runs", "--run-a", str(self.run_a), "--run-b", str(self.run_b), "--db-path", self.db]
        )
        self.assertEqual(code, 0, err)
        self.assertIn(f"Run #{self.run_a} vs Run #{self.run_b}", out)
        self.assertIn("Changed files:", out)
        self.assertIn("Summary:", out)

    def test_compare_runs_show_artifacts(self):
        code, out, err = run_cli(
            ["compare", "runs", "--run-a", str(self.run_a), "--run-b", str(self.run_b),
             "--show-artifacts", "--db-path", self.db]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Artifact counts by type:", out)
        self.assertIn("changed_files=1", out)

    def test_compare_runs_missing_run(self):
        code, out, err = run_cli(
            ["compare", "runs", "--run-a", str(self.run_a), "--run-b", "9999", "--db-path", self.db]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())

    def test_compare_same_run_rejected(self):
        code, out, err = run_cli(
            ["compare", "runs", "--run-a", str(self.run_a), "--run-b", str(self.run_a), "--db-path", self.db]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("itself", err.lower())


class ChainCliTests(_DbTestCase):
    def setUp(self):
        super().setUp()
        storage.init_db(self.db)
        self.run_id = storage.create_run(
            self.db, root_prompt="Build it", provider="mock", max_loops=2, require_approval=True
        )
        s0 = storage.create_step(self.db, self.run_id, 0, "step zero", "DONE", exit_code=0, next_prompt="go on")
        storage.create_artifact(self.db, self.run_id, "changed_files", content="src/a.py", step_id=s0)
        storage.create_approval(self.db, self.run_id, s0, "go on", status="APPROVED")
        self.s1 = storage.create_step(
            self.db, self.run_id, 1, "step one", "FAILED", exit_code=1, stderr="boom", next_prompt="fix"
        )

    def test_chain_show(self):
        code, out, err = run_cli(["chain", "show", "--run-id", str(self.run_id), "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn(f"Chain for run #{self.run_id}", out)
        self.assertIn("[loop 0]", out)
        self.assertIn("[loop 1]", out)
        self.assertIn("approval: APPROVED", out)

    def test_chain_show_artifacts(self):
        code, out, err = run_cli(
            ["chain", "show", "--run-id", str(self.run_id), "--artifacts", "--db-path", self.db]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("changed_files=1", out)

    def test_chain_show_errors_only(self):
        code, out, err = run_cli(
            ["chain", "show", "--run-id", str(self.run_id), "--errors-only", "--db-path", self.db]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("[loop 1]", out)
        self.assertNotIn("[loop 0]", out)

    def test_chain_show_missing_run(self):
        code, out, err = run_cli(["chain", "show", "--run-id", "9999", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())


class ProviderCliTests(_DbTestCase):
    def setUp(self):
        super().setUp()
        storage.init_db(self.db)

    def test_provider_seed_and_list(self):
        code, out, err = run_cli(["provider", "seed", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("seeded", out)
        code, out, err = run_cli(["provider", "list", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("mock", out)
        self.assertIn("claude-code", out)
        self.assertIn("available", out)

    def test_provider_add_show_update(self):
        code, out, err = run_cli(
            ["provider", "add", "--name", "claude-fast", "--type", "claude-code",
             "--command", "claude", "--timeout-seconds", "1200", "--db-path", self.db]
        )
        self.assertEqual(code, 0, err)
        code, out, err = run_cli(["provider", "show", "--name", "claude-fast", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("claude-code", out)
        self.assertIn("1200", out)
        code, out, err = run_cli(
            ["provider", "update", "--name", "claude-fast", "--timeout-seconds", "1800", "--db-path", self.db]
        )
        self.assertEqual(code, 0, err)

    def test_provider_add_invalid_type(self):
        code, out, err = run_cli(
            ["provider", "add", "--name", "z", "--type", "nope", "--command", "x", "--db-path", self.db]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("unsupported provider type", err)

    def test_provider_check_available_and_missing(self):
        run_cli(["provider", "seed", "--db-path", self.db])
        code, out, err = run_cli(["provider", "check", "--name", "mock", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("available", out)
        code, out, err = run_cli(["provider", "check", "--name", "claude-code", "--db-path", self.db])
        self.assertNotEqual(code, 0)  # 'claude' not installed in tests
        code, out, err = run_cli(["provider", "check", "--name", "nope", "--db-path", self.db])
        self.assertNotEqual(code, 0)
        self.assertIn("not found", err.lower())

    def test_provider_enable_disable(self):
        run_cli(["provider", "seed", "--db-path", self.db])
        code, out, err = run_cli(["provider", "disable", "--name", "mock", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertFalse(storage.get_provider_profile_by_name(self.db, "mock").enabled)
        code, out, err = run_cli(["provider", "enable", "--name", "mock", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertTrue(storage.get_provider_profile_by_name(self.db, "mock").enabled)


class RecoveryCliTests(_DbTestCase):
    def setUp(self):
        super().setUp()
        storage.init_db(self.db)
        self.run_id = storage.create_run(
            self.db, root_prompt="Fix signup", provider="mock", max_loops=1, require_approval=False
        )
        storage.create_step(self.db, self.run_id, 0, "run tests", "FAILED", stderr="AssertionError", exit_code=1)
        storage.update_run_status(self.db, self.run_id, RunStatus.FAILED.value)

    def test_recovery_propose_and_list(self):
        code, out, err = run_cli(["recovery", "propose", "--run-id", str(self.run_id), "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("proposed", out.lower())
        code, out, err = run_cli(["recovery", "list", "--run-id", str(self.run_id), "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("PROPOSED", out)

    def test_recovery_propose_show_prompt_uses_stderr(self):
        code, out, err = run_cli(
            ["recovery", "propose", "--run-id", str(self.run_id), "--show-prompt", "--db-path", self.db]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("stderr", out)

    def test_recovery_propose_non_failed_run(self):
        ok = storage.create_run(self.db, root_prompt="ok", provider="mock", max_loops=1, require_approval=False)
        storage.update_run_status(self.db, ok, RunStatus.RUNNING.value)
        storage.update_run_status(self.db, ok, RunStatus.DONE.value)
        code, out, err = run_cli(["recovery", "propose", "--run-id", str(ok), "--db-path", self.db])
        self.assertNotEqual(code, 0)

    def test_recovery_approve_reject(self):
        rid = storage.create_recovery_attempt(self.db, self.run_id, "recover please")
        code, out, err = run_cli(["recovery", "approve", "--id", str(rid), "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertEqual(storage.get_recovery_attempt(self.db, rid).status, "APPROVED")
        rid2 = storage.create_recovery_attempt(self.db, self.run_id, "recover please")
        code, out, err = run_cli(["recovery", "reject", "--id", str(rid2), "--reason", "no", "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertEqual(storage.get_recovery_attempt(self.db, rid2).status, "REJECTED")

    def test_recovery_execute_creates_linked_run(self):
        rid = storage.create_recovery_attempt(self.db, self.run_id, "recover please")
        code, out, err = run_cli(["recovery", "execute", "--id", str(rid), "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIsNotNone(storage.get_recovery_attempt(self.db, rid).recovery_run_id)


class ExportImportCliTests(_DbTestCase):
    def setUp(self):
        super().setUp()
        storage.init_db(self.db)
        storage.create_template(self.db, name="Cont", body="do {{goal}}", tags=["x"])
        run_id = storage.create_run(
            self.db, root_prompt="Fix it", provider="mock", max_loops=1, require_approval=False
        )
        storage.create_step(self.db, run_id, 0, "run", "FAILED", stderr="boom", exit_code=1)
        storage.update_run_status(self.db, run_id, RunStatus.FAILED.value)
        self.out = os.path.join(self.ws, "export.json")

    def test_export_data(self):
        code, out, err = run_cli(["export", "data", "--output", self.out, "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(self.out))
        self.assertIn("runs=1", out)

    def test_export_summary(self):
        run_cli(["export", "data", "--output", self.out, "--db-path", self.db])
        code, out, err = run_cli(["export", "summary", "--input", self.out, "--db-path", self.db])
        self.assertEqual(code, 0, err)
        self.assertIn("templates=1", out)

    def test_import_data(self):
        run_cli(["export", "data", "--output", self.out, "--db-path", self.db])
        dst = os.path.join(self.ws, "dst.db")
        code, out, err = run_cli(["import", "data", "--input", self.out, "--mode", "merge", "--db-path", dst])
        self.assertEqual(code, 0, err)
        self.assertIn("imported", out.lower())
        self.assertEqual(len(storage.list_runs(dst)), 1)

    def test_import_invalid_file_exits_nonzero(self):
        bad = os.path.join(self.ws, "bad.json")
        with open(bad, "w", encoding="utf-8") as handle:
            handle.write("not json")
        code, out, err = run_cli(["import", "data", "--input", bad, "--db-path", self.db])
        self.assertNotEqual(code, 0)


class EntryPointTests(unittest.TestCase):
    def test_main_is_callable(self):
        self.assertTrue(callable(main))

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_no_command_prints_usage(self):
        code, out, err = run_cli([])
        self.assertEqual(code, 2)  # EXIT_USAGE


class AuthCliTests(unittest.TestCase):
    _ENV = ("AUTOPROMPT_AUTH_ENABLED", "AUTOPROMPT_API_TOKEN")

    def setUp(self):
        for name in self._ENV:
            os.environ.pop(name, None)

    def tearDown(self):
        for name in self._ENV:
            os.environ.pop(name, None)

    def test_auth_token_generate(self):
        code, out, err = run_cli(["auth", "token", "generate"])
        self.assertEqual(code, 0, err)
        token = out.strip()
        self.assertGreaterEqual(len(token), 32)
        self.assertNotIn(" ", token)

    def test_config_validate_fails_when_auth_enabled_without_token(self):
        os.environ["AUTOPROMPT_AUTH_ENABLED"] = "true"
        os.environ["AUTOPROMPT_API_TOKEN"] = ""
        code, out, err = run_cli(["config", "validate"])
        self.assertNotEqual(code, 0)
        self.assertIn("api_token", err)

    def test_config_show_redacts_token(self):
        os.environ["AUTOPROMPT_AUTH_ENABLED"] = "true"
        os.environ["AUTOPROMPT_API_TOKEN"] = "supersecret-value"
        code, out, err = run_cli(["config", "show"])
        self.assertEqual(code, 0, err)
        self.assertIn("(set, redacted)", out)
        self.assertNotIn("supersecret-value", out)


if __name__ == "__main__":
    unittest.main()
