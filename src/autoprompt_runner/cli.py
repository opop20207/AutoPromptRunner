"""Command-line interface for AutoPromptRunner.

CLI-first entry point (see PROJECT.md). Commands:

* ``version``        -- print the package version.
* ``init-db``        -- create the local SQLite database.
* ``project``        -- manage project profiles (add / list / show / set-default / delete).
* ``template``       -- manage reusable prompt templates (seed / list / show / add /
  delete / render).
* ``worktree``       -- manage isolated Git worktrees for parallel sessions (create /
  list / show / archive / remove).
* ``locks``          -- list or manually release workspace execution locks.
* ``queue``          -- list or cancel queued run jobs.
* ``worker``         -- run the local background queue worker (executes queued runs).
* ``run``            -- start a run; execute the first step, generate the next prompt,
  and pause at a pending approval (default) or auto-run up to ``--max-loops``.
* ``approve-next``   -- approve a run's pending next prompt and execute it.
* ``reject-next``    -- reject a run's pending next prompt and stop the run.
* ``list-runs``      -- list recent runs.
* ``show-run``       -- show one run, its steps (with changed files / diff stat), and
  any pending approval.
* ``show-artifacts`` -- list a run's captured artifacts (Git state + runner output).
* ``show-artifact``  -- print one artifact's full content.

A project profile stores reusable run settings; ``run`` resolves settings with
precedence: explicit flags > selected ``--project`` > default project > built-in
defaults. Providers: ``mock`` (offline, default) and ``claude-code``.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional, Sequence

from . import __version__, auth, cancel, chains, checkpoints, compare, export_import, locks, providers, queue, reconcile, recovery, safety, search, settings, storage, templates, worker, worktrees
from .artifacts import ArtifactType
from .models import StepExecutionReport
from .services.run_service import (
    DEFAULT_PROVIDER_FACTORIES,
    RunInputError,
    RunService,
    RunServiceError,
    resolve_run_inputs,
)
from .state import RunStatus

# Provider names the CLI accepts (resolution/construction lives in RunService).
SUPPORTED_PROVIDERS = tuple(sorted(DEFAULT_PROVIDER_FACTORIES))

# Exit codes.
EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 4


def _add_db_path(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db-path",
        dest="db_path",
        default=None,
        help="SQLite database path. Defaults to .autoprompt/autoprompt.db.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser and all subcommands."""
    parser = argparse.ArgumentParser(
        prog="autoprompt-runner",
        description="Local-first prompt orchestration tool (CLI).",
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to a TOML config file (overrides the search order). Pass before the command.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    subparsers.add_parser("version", help="Print the package version.")

    init_parser = subparsers.add_parser("init-db", help="Create the local SQLite database.")
    _add_db_path(init_parser)

    safety_parser = subparsers.add_parser(
        "safety-check", help="Run prompt/workspace safety checks without executing an agent."
    )
    safety_parser.add_argument("--prompt", required=True, help="Prompt to scan for blocked command patterns.")
    safety_parser.add_argument("--workspace", default=None, help="Workspace directory to validate (optional).")

    _add_project_commands(subparsers)
    _add_template_commands(subparsers)
    _add_worktree_commands(subparsers)
    _add_locks_commands(subparsers)
    _add_queue_commands(subparsers)
    _add_worker_commands(subparsers)
    _add_config_commands(subparsers)
    _add_search_commands(subparsers)
    _add_compare_commands(subparsers)
    _add_chain_commands(subparsers)
    _add_provider_commands(subparsers)
    _add_recovery_commands(subparsers)
    _add_checkpoint_commands(subparsers)
    _add_export_import_commands(subparsers)
    _add_auth_commands(subparsers)
    _add_system_commands(subparsers)

    run_parser = subparsers.add_parser(
        "run", help="Start a run; pause at an approval gate by default."
    )
    run_parser.add_argument(
        "--project", default=None,
        help="Use settings from this project profile (defaults to the default project).",
    )
    run_parser.add_argument(
        "--prompt", default=None,
        help="Prompt to send. Provide this or --template (not both).",
    )
    run_parser.add_argument(
        "--template", default=None,
        help="Render this template's body as the prompt (mutually exclusive with --prompt).",
    )
    run_parser.add_argument(
        "--goal", default=None, help="Value for the {{goal}} placeholder when using --template.",
    )
    run_parser.add_argument(
        "--extra-context", dest="extra_context", default=None,
        help="Value for the {{extra_context}} placeholder when using --template.",
    )
    run_parser.add_argument(
        "--provider", default=None,
        help="Provider: mock or claude-code. Overrides the project default.",
    )
    run_parser.add_argument(
        "--max-loops", dest="max_loops", type=int, default=None,
        help="Max agent invocations (>= 1). Overrides the project default.",
    )
    run_parser.add_argument(
        "--no-approval", dest="no_approval", action="store_true",
        help="Disable the approval gate and auto-run up to --max-loops.",
    )
    run_parser.add_argument(
        "--workspace", default=None,
        help="Workspace directory. Required for claude-code; defaults to the project repo_path.",
    )
    run_parser.add_argument(
        "--worktree", default=None,
        help="Run inside this Git worktree's path (overridden by an explicit --workspace).",
    )
    run_parser.add_argument(
        "--timeout-seconds", dest="timeout_seconds", type=int, default=None,
        help="Subprocess timeout in seconds (>= 1). Overrides the project default.",
    )
    run_parser.add_argument(
        "--show-next-prompt", dest="show_next_prompt", action="store_true",
        help="Print the full generated next prompt instead of only a compact preview.",
    )
    run_parser.add_argument(
        "--queued", dest="queued", action="store_true",
        help="Create and enqueue the run for a background worker instead of running it now.",
    )
    _add_db_path(run_parser)

    # `run cancel --run-id N` cancels a run; `run` without a subcommand starts one.
    run_sub = run_parser.add_subparsers(dest="run_command", metavar="subcommand")
    run_cancel_parser = run_sub.add_parser("cancel", help="Cancel a queued/running/waiting run and stop it.")
    run_cancel_parser.add_argument("--run-id", dest="run_id", type=int, required=True, help="Run id to cancel.")
    run_cancel_parser.add_argument("--reason", default=None, help="Optional human-readable cancellation reason.")
    _add_db_path(run_cancel_parser)

    approve_parser = subparsers.add_parser(
        "approve-next", help="Approve a run's pending next prompt and execute it."
    )
    approve_parser.add_argument("--run-id", dest="run_id", type=int, required=True, help="Run id.")
    approve_parser.add_argument(
        "--show-next-prompt", dest="show_next_prompt", action="store_true",
        help="Print the full generated next prompt instead of only a compact preview.",
    )
    _add_db_path(approve_parser)

    reject_parser = subparsers.add_parser(
        "reject-next", help="Reject a run's pending next prompt and stop the run."
    )
    reject_parser.add_argument("--run-id", dest="run_id", type=int, required=True, help="Run id.")
    _add_db_path(reject_parser)

    list_parser = subparsers.add_parser("list-runs", help="List recent runs.")
    list_parser.add_argument("--limit", type=int, default=20, help="Maximum number of runs to show.")
    _add_db_path(list_parser)

    show_parser = subparsers.add_parser("show-run", help="Show one run, its steps, and pending approval.")
    show_parser.add_argument("--id", dest="run_id", type=int, required=True, help="Run id to show.")
    _add_db_path(show_parser)

    artifacts_parser = subparsers.add_parser("show-artifacts", help="List a run's artifacts.")
    artifacts_parser.add_argument("--run-id", dest="run_id", type=int, required=True, help="Run id.")
    artifacts_parser.add_argument("--type", dest="type", default=None, help="Filter by artifact type.")
    _add_db_path(artifacts_parser)

    artifact_parser = subparsers.add_parser("show-artifact", help="Print one artifact's full content.")
    artifact_parser.add_argument("--id", dest="artifact_id", type=int, required=True, help="Artifact id.")
    _add_db_path(artifact_parser)

    return parser


def _add_project_commands(subparsers: argparse._SubParsersAction) -> None:
    project_parser = subparsers.add_parser("project", help="Manage project profiles.")
    project_sub = project_parser.add_subparsers(dest="project_command", metavar="subcommand")

    add_parser = project_sub.add_parser("add", help="Add a project profile.")
    add_parser.add_argument("--name", required=True, help="Unique project name.")
    add_parser.add_argument("--repo-path", dest="repo_path", required=True, help="Project directory (must exist).")
    add_parser.add_argument("--provider", default="mock", help="Default provider: mock or claude-code.")
    add_parser.add_argument("--max-loops", dest="max_loops", type=int, default=1, help="Default max loops (>= 1).")
    add_parser.add_argument(
        "--timeout-seconds", dest="timeout_seconds", type=int, default=1800, help="Default timeout seconds (>= 1)."
    )
    add_parser.add_argument(
        "--no-approval", dest="no_approval", action="store_true",
        help="Store require_approval = false for this project.",
    )
    _add_db_path(add_parser)

    list_parser = project_sub.add_parser("list", help="List project profiles.")
    _add_db_path(list_parser)

    show_parser = project_sub.add_parser("show", help="Show a project profile.")
    show_parser.add_argument("--name", required=True, help="Project name.")
    _add_db_path(show_parser)

    setdef_parser = project_sub.add_parser("set-default", help="Set the default project.")
    setdef_parser.add_argument("--name", required=True, help="Project name.")
    _add_db_path(setdef_parser)

    delete_parser = project_sub.add_parser(
        "delete", help="Delete a project profile (files on disk are not touched)."
    )
    delete_parser.add_argument("--name", required=True, help="Project name.")
    _add_db_path(delete_parser)


def _add_template_commands(subparsers: argparse._SubParsersAction) -> None:
    template_parser = subparsers.add_parser("template", help="Manage reusable prompt templates.")
    template_sub = template_parser.add_subparsers(dest="template_command", metavar="subcommand")

    seed_parser = template_sub.add_parser("seed", help="Insert the built-in templates if missing.")
    seed_parser.add_argument(
        "--force", dest="force", action="store_true",
        help="Overwrite existing built-in templates instead of skipping them.",
    )
    _add_db_path(seed_parser)

    list_parser = template_sub.add_parser("list", help="List templates (id, name, tags, description).")
    _add_db_path(list_parser)

    show_parser = template_sub.add_parser("show", help="Print a template's full body.")
    show_parser.add_argument("--name", required=True, help="Template name.")
    _add_db_path(show_parser)

    add_parser = template_sub.add_parser("add", help="Create a custom template.")
    add_parser.add_argument("--name", required=True, help="Unique template name.")
    add_parser.add_argument("--description", default="", help="Short description.")
    add_parser.add_argument("--body", required=True, help="Template body (may contain {{placeholders}}).")
    add_parser.add_argument("--tags", default="", help="Comma-separated tags (optional).")
    _add_db_path(add_parser)

    delete_parser = template_sub.add_parser("delete", help="Delete a template (runs are not affected).")
    delete_parser.add_argument("--name", required=True, help="Template name.")
    _add_db_path(delete_parser)

    render_parser = template_sub.add_parser("render", help="Print a template rendered with the given values.")
    render_parser.add_argument("--name", required=True, help="Template name.")
    render_parser.add_argument("--project", default=None, help="Project name for {{project_name}}/{{workspace}}.")
    render_parser.add_argument("--workspace", default=None, help="Override the {{workspace}} value.")
    render_parser.add_argument("--goal", default=None, help="Value for {{goal}}.")
    render_parser.add_argument(
        "--extra-context", dest="extra_context", default=None, help="Value for {{extra_context}}."
    )
    _add_db_path(render_parser)


def _add_worktree_commands(subparsers: argparse._SubParsersAction) -> None:
    worktree_parser = subparsers.add_parser("worktree", help="Manage isolated Git worktrees for parallel sessions.")
    worktree_sub = worktree_parser.add_subparsers(dest="worktree_command", metavar="subcommand")

    create_parser = worktree_sub.add_parser("create", help="Create a Git worktree and record it.")
    create_parser.add_argument("--project", required=True, help="Project whose repo the worktree branches from.")
    create_parser.add_argument("--name", required=True, help="Unique worktree name (one safe path component).")
    create_parser.add_argument("--branch", required=True, help="New branch to create for the worktree.")
    create_parser.add_argument("--base-branch", dest="base_branch", default=None, help="Branch/commit to start from.")
    _add_db_path(create_parser)

    list_parser = worktree_sub.add_parser("list", help="List recorded worktrees (optionally by project).")
    list_parser.add_argument("--project", default=None, help="Only list worktrees for this project.")
    _add_db_path(list_parser)

    show_parser = worktree_sub.add_parser("show", help="Show a worktree's detail.")
    show_parser.add_argument("--name", required=True, help="Worktree name.")
    _add_db_path(show_parser)

    archive_parser = worktree_sub.add_parser("archive", help="Mark a worktree ARCHIVED (disk files are kept).")
    archive_parser.add_argument("--name", required=True, help="Worktree name.")
    _add_db_path(archive_parser)

    remove_parser = worktree_sub.add_parser(
        "remove", help="Remove a worktree via 'git worktree remove' and delete its record."
    )
    remove_parser.add_argument("--name", required=True, help="Worktree name.")
    remove_parser.add_argument(
        "--force", dest="force", action="store_true",
        help="Pass --force to git worktree remove and override the active-run guard.",
    )
    _add_db_path(remove_parser)


def _add_locks_commands(subparsers: argparse._SubParsersAction) -> None:
    locks_parser = subparsers.add_parser("locks", help="List or release workspace execution locks.")
    locks_sub = locks_parser.add_subparsers(dest="locks_command", metavar="subcommand")

    list_parser = locks_sub.add_parser("list", help="List active/recent workspace locks.")
    list_parser.add_argument("--limit", type=int, default=50, help="Maximum number of locks to show.")
    _add_db_path(list_parser)

    release_parser = locks_sub.add_parser(
        "release", help="Manually release a run's workspace lock (escape hatch for stale locks)."
    )
    release_parser.add_argument("--run-id", dest="run_id", type=int, required=True, help="Run id whose lock to release.")
    _add_db_path(release_parser)


def _add_queue_commands(subparsers: argparse._SubParsersAction) -> None:
    queue_parser = subparsers.add_parser("queue", help="List or cancel queued run jobs.")
    queue_sub = queue_parser.add_subparsers(dest="queue_command", metavar="subcommand")

    list_parser = queue_sub.add_parser("list", help="List queued/running/recent jobs.")
    list_parser.add_argument("--limit", type=int, default=50, help="Maximum number of jobs to show.")
    _add_db_path(list_parser)

    cancel_parser = queue_sub.add_parser("cancel", help="Cancel a queued job (running jobs cannot be killed yet).")
    cancel_parser.add_argument("--run-id", dest="run_id", type=int, required=True, help="Run id whose job to cancel.")
    _add_db_path(cancel_parser)


def _add_worker_commands(subparsers: argparse._SubParsersAction) -> None:
    worker_parser = subparsers.add_parser("worker", help="Run the local background queue worker.")
    worker_sub = worker_parser.add_subparsers(dest="worker_command", metavar="subcommand")

    run_parser = worker_sub.add_parser("run", help="Start the queue worker loop (Ctrl+C to stop).")
    run_parser.add_argument("--once", dest="once", action="store_true", help="Execute one job if available, then exit.")
    run_parser.add_argument(
        "--poll-interval-seconds", dest="poll_interval_seconds", type=float, default=None,
        help="Seconds between polls when the queue is empty (default: config queue.poll_interval_seconds).",
    )
    run_parser.add_argument(
        "--reconcile-on-start", dest="reconcile_on_start", action=argparse.BooleanOptionalAction, default=True,
        help="Reconcile stale state before polling (default: on; --no-reconcile-on-start to skip).",
    )
    _add_db_path(run_parser)


def _add_config_commands(subparsers: argparse._SubParsersAction) -> None:
    config_parser = subparsers.add_parser("config", help="Inspect or initialize configuration.")
    config_sub = config_parser.add_subparsers(dest="config_command", metavar="subcommand")
    config_sub.add_parser("show", help="Print the effective config (file + env; no secrets).")
    config_sub.add_parser("validate", help="Validate the effective config; exit non-zero if invalid.")
    init_parser = config_sub.add_parser("init", help="Create .autoprompt/config.toml from the defaults.")
    init_parser.add_argument("--force", dest="force", action="store_true", help="Overwrite an existing config file.")


def _add_auth_commands(subparsers: argparse._SubParsersAction) -> None:
    auth_parser = subparsers.add_parser("auth", help="Manage optional local API token authentication.")
    auth_sub = auth_parser.add_subparsers(dest="auth_command", metavar="subcommand")
    token_parser = auth_sub.add_parser("token", help="API token helpers.")
    token_sub = token_parser.add_subparsers(dest="token_command", metavar="subcommand")
    token_sub.add_parser("generate", help="Generate and print a new secure API token (not saved).")


def _add_system_commands(subparsers: argparse._SubParsersAction) -> None:
    system_parser = subparsers.add_parser("system", help="Inspect and reconcile stale state (crash recovery).")
    system_sub = system_parser.add_subparsers(dest="system_command", metavar="subcommand")
    status_parser = system_sub.add_parser("status", help="Print workers / jobs / locks / stale-run state.")
    _add_db_path(status_parser)
    reconcile_parser = system_sub.add_parser("reconcile", help="Reconcile stale state (--dry-run to only report).")
    reconcile_parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true", help="Report only; do not modify the database."
    )
    _add_db_path(reconcile_parser)


def _add_search_commands(subparsers: argparse._SubParsersAction) -> None:
    search_parser = subparsers.add_parser("search", help="Search runs, steps, and artifacts (SQLite LIKE).")
    search_sub = search_parser.add_subparsers(dest="search_command", metavar="subcommand")

    runs_parser = search_sub.add_parser("runs", help="Search runs by prompt / provider / status.")
    runs_parser.add_argument("--query", default=None, help="Text to match (case-insensitive).")
    runs_parser.add_argument("--status", default=None, help="Filter by run status.")
    runs_parser.add_argument("--provider", default=None, help="Filter by provider.")
    runs_parser.add_argument("--limit", type=int, default=50, help="Max results (hard cap 200).")
    runs_parser.add_argument("--offset", type=int, default=0, help="Result offset for pagination.")
    _add_db_path(runs_parser)

    artifacts_parser = search_sub.add_parser("artifacts", help="Search artifacts by content / type / path.")
    artifacts_parser.add_argument("--query", default=None, help="Text to match (case-insensitive).")
    artifacts_parser.add_argument("--type", dest="type", default=None, help="Filter by artifact type.")
    artifacts_parser.add_argument("--limit", type=int, default=50, help="Max results (hard cap 200).")
    artifacts_parser.add_argument("--offset", type=int, default=0, help="Result offset for pagination.")
    _add_db_path(artifacts_parser)

    all_parser = search_sub.add_parser("all", help="Search runs, steps, and artifacts together.")
    all_parser.add_argument("--query", default=None, help="Text to match (case-insensitive).")
    all_parser.add_argument("--limit", type=int, default=50, help="Max results per group (hard cap 200).")
    all_parser.add_argument("--offset", type=int, default=0, help="Result offset for pagination.")
    _add_db_path(all_parser)


def _add_compare_commands(subparsers: argparse._SubParsersAction) -> None:
    compare_parser = subparsers.add_parser("compare", help="Compare two runs (stored content only).")
    compare_sub = compare_parser.add_subparsers(dest="compare_command", metavar="subcommand")

    runs_parser = compare_sub.add_parser("runs", help="Compare two runs side by side.")
    runs_parser.add_argument("--run-a", dest="run_a", type=int, required=True, help="First run id.")
    runs_parser.add_argument("--run-b", dest="run_b", type=int, required=True, help="Second run id.")
    runs_parser.add_argument(
        "--show-prompts", action="store_true",
        help="Print the full root and latest next prompt text (default: compact previews).",
    )
    runs_parser.add_argument(
        "--show-artifacts", action="store_true", help="Print artifact counts by type."
    )
    _add_db_path(runs_parser)


def _add_chain_commands(subparsers: argparse._SubParsersAction) -> None:
    chain_parser = subparsers.add_parser("chain", help="Show a run's prompt chain history.")
    chain_sub = chain_parser.add_subparsers(dest="chain_command", metavar="subcommand")

    show_parser = chain_sub.add_parser("show", help="Print a run's prompt chain timeline.")
    show_parser.add_argument("--run-id", dest="run_id", type=int, required=True, help="Run id.")
    show_parser.add_argument(
        "--full-prompts", action="store_true", help="Print the full prompt and next prompt text."
    )
    show_parser.add_argument(
        "--artifacts", action="store_true", help="Print artifact counts by type per node."
    )
    show_parser.add_argument(
        "--errors-only", action="store_true", help="Show failed/error nodes only."
    )
    _add_db_path(show_parser)


def _add_provider_commands(subparsers: argparse._SubParsersAction) -> None:
    provider_parser = subparsers.add_parser("provider", help="Manage provider profiles (command/timeout config).")
    provider_sub = provider_parser.add_subparsers(dest="provider_command", metavar="subcommand")

    seed_parser = provider_sub.add_parser("seed", help="Create the default provider profiles if missing.")
    seed_parser.add_argument("--force", action="store_true", help="Reset existing default profiles to defaults.")
    _add_db_path(seed_parser)

    list_parser = provider_sub.add_parser("list", help="List provider profiles with availability.")
    _add_db_path(list_parser)

    show_parser = provider_sub.add_parser("show", help="Show one provider profile and its availability.")
    show_parser.add_argument("--name", required=True, help="Provider profile name.")
    _add_db_path(show_parser)

    add_parser = provider_sub.add_parser("add", help="Create a provider profile.")
    add_parser.add_argument("--name", required=True, help="Unique profile name.")
    add_parser.add_argument("--type", required=True, help="Provider type: mock, claude-code, or codex.")
    # dest avoids colliding with the top-level subparser dest "command".
    add_parser.add_argument("--command", dest="command_exec", required=True, help="Executable to invoke (no arguments).")
    add_parser.add_argument(
        "--timeout-seconds", dest="timeout_seconds", type=int, default=1800, help="Default timeout (>= 1)."
    )
    add_parser.add_argument("--default-args", dest="default_args", default=None, help="Space-separated default args.")
    add_parser.add_argument("--disabled", action="store_true", help="Create the profile disabled.")
    _add_db_path(add_parser)

    update_parser = provider_sub.add_parser("update", help="Update editable fields of a provider profile.")
    update_parser.add_argument("--name", required=True, help="Profile to update.")
    update_parser.add_argument("--type", default=None, help="New provider type.")
    update_parser.add_argument("--command", dest="command_exec", default=None, help="New command.")
    update_parser.add_argument(
        "--timeout-seconds", dest="timeout_seconds", type=int, default=None, help="New default timeout."
    )
    update_parser.add_argument("--default-args", dest="default_args", default=None, help="New default args.")
    _add_db_path(update_parser)

    enable_parser = provider_sub.add_parser("enable", help="Enable a provider profile.")
    enable_parser.add_argument("--name", required=True, help="Profile name.")
    _add_db_path(enable_parser)

    disable_parser = provider_sub.add_parser("disable", help="Disable a provider profile.")
    disable_parser.add_argument("--name", required=True, help="Profile name.")
    _add_db_path(disable_parser)

    delete_parser = provider_sub.add_parser("delete", help="Delete a provider profile (no external tool is removed).")
    delete_parser.add_argument("--name", required=True, help="Profile name.")
    _add_db_path(delete_parser)

    check_parser = provider_sub.add_parser("check", help="Check a provider's command availability.")
    check_parser.add_argument("--name", required=True, help="Profile name.")
    _add_db_path(check_parser)


def _add_recovery_commands(subparsers: argparse._SubParsersAction) -> None:
    recovery_parser = subparsers.add_parser("recovery", help="Propose and run failure recovery for a failed run.")
    recovery_sub = recovery_parser.add_subparsers(dest="recovery_command", metavar="subcommand")

    propose_parser = recovery_sub.add_parser("propose", help="Propose a recovery for a FAILED run.")
    propose_parser.add_argument("--run-id", dest="run_id", type=int, required=True, help="The FAILED run id.")
    propose_parser.add_argument("--reason", default=None, help="Optional note for the recovery attempt.")
    propose_parser.add_argument("--show-prompt", action="store_true", help="Print the full recovery prompt.")
    _add_db_path(propose_parser)

    approve_parser = recovery_sub.add_parser("approve", help="Approve a recovery attempt.")
    approve_parser.add_argument("--id", dest="recovery_id", type=int, required=True, help="Recovery attempt id.")
    approve_parser.add_argument("--execute", action="store_true", help="Execute the recovery after approving.")
    approve_parser.add_argument("--queued", action="store_true", help="Queue the recovery run (with --execute).")
    _add_db_path(approve_parser)

    reject_parser = recovery_sub.add_parser("reject", help="Reject a recovery attempt.")
    reject_parser.add_argument("--id", dest="recovery_id", type=int, required=True, help="Recovery attempt id.")
    reject_parser.add_argument("--reason", default=None, help="Optional rejection reason.")
    _add_db_path(reject_parser)

    execute_parser = recovery_sub.add_parser("execute", help="Execute a recovery attempt (creates a linked run).")
    execute_parser.add_argument("--id", dest="recovery_id", type=int, required=True, help="Recovery attempt id.")
    execute_parser.add_argument("--queued", action="store_true", help="Queue the recovery run for a worker.")
    _add_db_path(execute_parser)

    list_parser = recovery_sub.add_parser("list", help="List recovery attempts (optionally for one run).")
    list_parser.add_argument("--run-id", dest="run_id", type=int, default=None, help="Filter by source run id.")
    _add_db_path(list_parser)


def _add_checkpoint_commands(subparsers: argparse._SubParsersAction) -> None:
    cp_parser = subparsers.add_parser(
        "checkpoint", help="Inspect run checkpoints and roll a workspace back (explicit, Git-only)."
    )
    cp_sub = cp_parser.add_subparsers(dest="checkpoint_command", metavar="subcommand")

    list_parser = cp_sub.add_parser("list", help="List checkpoints for a run.")
    list_parser.add_argument("--run-id", dest="run_id", type=int, required=True, help="Run id.")
    _add_db_path(list_parser)

    show_parser = cp_sub.add_parser("show", help="Show a checkpoint's detail and its rollback plan.")
    show_parser.add_argument("--id", dest="checkpoint_id", type=int, required=True, help="Checkpoint id.")
    _add_db_path(show_parser)

    plan_parser = cp_sub.add_parser(
        "rollback-plan", help="Show what a rollback would do (read-only; changes nothing)."
    )
    plan_parser.add_argument("--id", dest="checkpoint_id", type=int, required=True, help="Checkpoint id.")
    _add_db_path(plan_parser)

    rollback_parser = cp_sub.add_parser(
        "rollback", help="Roll the workspace back to the checkpoint (git reset --hard; requires --confirm)."
    )
    rollback_parser.add_argument("--id", dest="checkpoint_id", type=int, required=True, help="Checkpoint id.")
    rollback_parser.add_argument(
        "--confirm", action="store_true", help="Required: confirm the destructive rollback."
    )
    rollback_parser.add_argument(
        "--force", action="store_true",
        help="Override the safety refusal when the workspace has changes not created by the run.",
    )
    _add_db_path(rollback_parser)


def _add_export_import_commands(subparsers: argparse._SubParsersAction) -> None:
    export_parser = subparsers.add_parser("export", help="Export AutoPromptRunner data to a JSON file.")
    export_sub = export_parser.add_subparsers(dest="export_command", metavar="subcommand")

    data_parser = export_sub.add_parser("data", help="Write a JSON export of projects/templates/providers/runs.")
    data_parser.add_argument("--output", required=True, help="Path to write the export JSON.")
    data_parser.add_argument("--run-id", dest="run_id", type=int, action="append", help="Export only this run id (repeatable).")
    data_parser.add_argument("--project", dest="project", action="append", help="Export only this project's runs (repeatable).")
    data_parser.add_argument("--no-projects", dest="include_projects", action="store_false", help="Exclude project profiles.")
    data_parser.add_argument("--no-providers", dest="include_providers", action="store_false", help="Exclude provider profiles.")
    data_parser.add_argument("--no-templates", dest="include_templates", action="store_false", help="Exclude templates.")
    data_parser.add_argument("--no-artifacts", dest="include_artifacts", action="store_false", help="Exclude artifacts.")
    data_parser.add_argument("--no-recoveries", dest="include_recoveries", action="store_false", help="Exclude recovery attempts.")
    data_parser.add_argument("--no-artifact-content", dest="artifact_content", action="store_false", help="Export artifact metadata without content.")
    data_parser.add_argument("--no-redact", dest="redact_sensitive", action="store_false", help="Do not redact secret-like artifact content.")
    _add_db_path(data_parser)

    summary_parser = export_sub.add_parser("summary", help="Print a summary of an export file without importing.")
    summary_parser.add_argument("--input", required=True, help="Path to an export JSON file.")
    _add_db_path(summary_parser)

    import_parser = subparsers.add_parser("import", help="Import AutoPromptRunner data from a JSON export.")
    import_sub = import_parser.add_subparsers(dest="import_command", metavar="subcommand")

    import_data_parser = import_sub.add_parser("data", help="Validate and import a JSON export file.")
    import_data_parser.add_argument("--input", required=True, help="Path to an export JSON file.")
    import_data_parser.add_argument(
        "--mode", default="merge", choices=list(export_import.IMPORT_MODES),
        help="Import mode: merge (default), skip_existing, or replace_templates_only.",
    )
    _add_db_path(import_data_parser)


# -- simple commands ---------------------------------------------------------


def cmd_version() -> int:
    print(__version__)
    return EXIT_OK


def cmd_init_db(args: argparse.Namespace) -> int:
    db_path = args.db_path or settings.load_settings(args.config).storage.db_path
    path = storage.init_db(db_path)
    print(f"Database initialized at: {path}")
    return EXIT_OK


def cmd_safety_check(args: argparse.Namespace) -> int:
    """Run prompt/workspace safety checks only (no agent execution)."""
    prompt = (args.prompt or "").strip()
    blockers = []
    warnings = []
    if not prompt:
        blockers.append("--prompt must not be empty")
    else:
        for pattern in safety.scan_prompt_for_blocked_commands(prompt):
            blockers.append(f"blocked command pattern in prompt: {pattern}")
    if args.workspace:
        if not os.path.isdir(args.workspace):
            blockers.append(f"workspace does not exist or is not a directory: {args.workspace}")
        else:
            try:
                safety.validate_workspace_allowed(args.workspace)
            except ValueError as exc:
                blockers.append(str(exc))

    print("Safety check")
    print("  blockers:")
    for item in blockers or ["none"]:
        print(f"    - {item}")
    print("  warnings:")
    for item in warnings or ["none"]:
        print(f"    - {item}")
    return EXIT_RUN_FAILED if blockers else EXIT_OK


# -- project commands --------------------------------------------------------


def cmd_project_add(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    name = (args.name or "").strip()
    if not name:
        print("error: --name must not be empty", file=sys.stderr)
        return EXIT_USAGE
    if args.provider not in DEFAULT_PROVIDER_FACTORIES:
        print(
            f"error: unsupported provider '{args.provider}'. Supported: {', '.join(SUPPORTED_PROVIDERS)}",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if args.max_loops < 1:
        print("error: --max-loops must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    if args.timeout_seconds < 1:
        print("error: --timeout-seconds must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    if not os.path.isdir(args.repo_path):
        print(f"error: --repo-path does not exist or is not a directory: {args.repo_path}", file=sys.stderr)
        return EXIT_USAGE
    if storage.get_project_by_name(db_path, name) is not None:
        print(f"error: project '{name}' already exists", file=sys.stderr)
        return EXIT_USAGE

    project_id = storage.create_project(
        db_path,
        name=name,
        repo_path=args.repo_path,
        default_provider=args.provider,
        default_max_loops=args.max_loops,
        require_approval=not args.no_approval,
        timeout_seconds=args.timeout_seconds,
    )
    print(f"Added project '{name}' (id {project_id}).")
    return EXIT_OK


def cmd_project_list(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    projects = storage.list_projects(db_path)
    if not projects:
        print("No projects.")
        return EXIT_OK
    default = storage.get_default_project(db_path)
    default_id = default.id if default is not None else None
    print(f"  {'NAME':<20}  {'PROVIDER':<12}  {'LOOPS':>5}  {'APPROVAL':<8}  {'TIMEOUT':>7}  REPO_PATH")
    for project in projects:
        marker = "*" if project.id == default_id else " "
        approval = "yes" if project.require_approval else "no"
        print(
            f"{marker} {project.name:<20}  {(project.default_provider or ''):<12}  "
            f"{str(project.default_max_loops):>5}  {approval:<8}  {str(project.timeout_seconds):>7}  "
            f"{project.repo_path}"
        )
    print("(* = default project)")
    return EXIT_OK


def cmd_project_show(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    project = storage.get_project_by_name(db_path, args.name)
    if project is None:
        print(f"error: project '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    default = storage.get_default_project(db_path)
    is_default = default is not None and default.id == project.id
    detail = [
        f"Project '{project.name}' (id {project.id})",
        f"  repo_path        : {project.repo_path}",
        f"  default_provider : {project.default_provider}",
        f"  default_max_loops: {project.default_max_loops}",
        f"  require_approval : {project.require_approval}",
        f"  timeout_seconds  : {project.timeout_seconds}",
        f"  is_default       : {is_default}",
        f"  created_at       : {project.created_at}",
        f"  updated_at       : {project.updated_at}",
    ]
    print("\n".join(detail))
    return EXIT_OK


def cmd_project_set_default(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    project = storage.get_project_by_name(db_path, args.name)
    if project is None:
        print(f"error: project '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    storage.set_default_project(db_path, project.id)
    print(f"Default project set to '{project.name}'.")
    return EXIT_OK


def cmd_project_delete(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    project = storage.get_project_by_name(db_path, args.name)
    if project is None:
        print(f"error: project '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    storage.delete_project(db_path, project.id)
    print(f"Deleted project '{project.name}'. Files on disk were not modified.")
    return EXIT_OK


# -- template commands -------------------------------------------------------


def _parse_tags(raw: Optional[str]) -> list:
    return [tag.strip() for tag in (raw or "").split(",") if tag.strip()]


def cmd_template_seed(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    result = templates.seed_templates(db_path, overwrite=args.force)
    verb = "Seeded/updated" if args.force else "Seeded"
    print(f"{verb} {result['seeded']} template(s); skipped {result['skipped']} existing.")
    return EXIT_OK


def cmd_template_list(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    items = templates.list_templates(db_path)
    if not items:
        print("No templates. Run 'template seed' to add the built-in templates.")
        return EXIT_OK
    print(f"{'ID':>4}  {'NAME':<28}  {'TAGS':<22}  DESCRIPTION")
    for tmpl in items:
        tags = ", ".join(tmpl.tags)
        print(f"{tmpl.id:>4}  {tmpl.name:<28}  {tags:<22}  {_shorten(tmpl.description, 50)}")
    return EXIT_OK


def cmd_template_show(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    tmpl = templates.get_template_by_name(db_path, args.name)
    if tmpl is None:
        print(f"error: template '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    print(f"Template '{tmpl.name}' (id {tmpl.id})")
    print(f"  description: {tmpl.description}")
    print(f"  tags       : {', '.join(tmpl.tags)}")
    print(f"  created_at : {tmpl.created_at}")
    print(f"  updated_at : {tmpl.updated_at}")
    print("---")
    print(tmpl.body)
    return EXIT_OK


def cmd_template_add(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    name = (args.name or "").strip()
    body = args.body or ""
    if not name:
        print("error: --name must not be empty", file=sys.stderr)
        return EXIT_USAGE
    if not body.strip():
        print("error: --body must not be empty", file=sys.stderr)
        return EXIT_USAGE
    if templates.get_template_by_name(db_path, name) is not None:
        print(f"error: template '{name}' already exists", file=sys.stderr)
        return EXIT_USAGE
    template_id = templates.create_template(
        db_path, name=name, body=body, description=args.description or "", tags=_parse_tags(args.tags)
    )
    print(f"Added template '{name}' (id {template_id}).")
    return EXIT_OK


def cmd_template_delete(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    tmpl = templates.get_template_by_name(db_path, args.name)
    if tmpl is None:
        print(f"error: template '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    templates.delete_template(db_path, tmpl.id)
    print(f"Deleted template '{tmpl.name}'. Runs were not affected.")
    return EXIT_OK


def cmd_template_render(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    tmpl = templates.get_template_by_name(db_path, args.name)
    if tmpl is None:
        print(f"error: template '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    project_name = ""
    workspace = args.workspace
    if args.project:
        project = storage.get_project_by_name(db_path, args.project)
        if project is None:
            print(f"error: project '{args.project}' not found", file=sys.stderr)
            return EXIT_NOT_FOUND
        project_name = project.name
        workspace = workspace or project.repo_path
    values = templates.build_render_values(
        project_name=project_name,
        workspace=workspace,
        goal=args.goal,
        extra_context=args.extra_context,
    )
    print(templates.render_template(tmpl.body, values))
    return EXIT_OK


def _dispatch_template(args: argparse.Namespace) -> int:
    handlers = {
        "seed": cmd_template_seed,
        "list": cmd_template_list,
        "show": cmd_template_show,
        "add": cmd_template_add,
        "delete": cmd_template_delete,
        "render": cmd_template_render,
    }
    handler = handlers.get(getattr(args, "template_command", None))
    if handler is None:
        print(
            "error: template requires a subcommand: seed, list, show, add, delete, render",
            file=sys.stderr,
        )
        return EXIT_USAGE
    return handler(args)


# -- worktree commands -------------------------------------------------------


def cmd_worktree_create(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    project = storage.get_project_by_name(db_path, args.project)
    if project is None:
        print(f"error: project '{args.project}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    if not project.repo_path or not os.path.isdir(project.repo_path):
        print(f"error: project repo_path does not exist or is not a directory: {project.repo_path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        name = worktrees.validate_worktree_name(args.name)
        branch = worktrees.validate_branch_name(args.branch)
    except worktrees.WorktreeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if storage.get_worktree_by_name(db_path, name) is not None:
        print(f"error: worktree '{name}' already exists", file=sys.stderr)
        return EXIT_USAGE
    try:
        path = worktrees.prepare_worktree_path(db_path, project.name, name)
        worktrees.create_git_worktree(project.repo_path, path, branch, args.base_branch)
    except worktrees.WorktreeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_RUN_FAILED
    worktree_id = storage.create_worktree_record(
        db_path, project_id=project.id, name=name, branch=branch, path=path,
        base_branch=args.base_branch, status=worktrees.WORKTREE_ACTIVE,
    )
    print(f"Created worktree '{name}' (id {worktree_id}) at {path} on branch {branch}.")
    return EXIT_OK


def cmd_worktree_list(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    if args.project:
        project = storage.get_project_by_name(db_path, args.project)
        if project is None:
            print(f"error: project '{args.project}' not found", file=sys.stderr)
            return EXIT_NOT_FOUND
        items = storage.list_worktrees_for_project(db_path, project.id)
    else:
        items = storage.list_worktrees(db_path)
    if not items:
        print("No worktrees.")
        return EXIT_OK
    print(f"{'ID':>4}  {'NAME':<20}  {'BRANCH':<24}  {'STATUS':<9}  PATH")
    for wt in items:
        print(f"{wt.id:>4}  {wt.name:<20}  {_shorten(wt.branch, 24):<24}  {wt.status:<9}  {wt.path}")
    return EXIT_OK


def cmd_worktree_show(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    wt = storage.get_worktree_by_name(db_path, args.name)
    if wt is None:
        print(f"error: worktree '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    project = storage.get_project_by_id(db_path, wt.project_id)
    detail = [
        f"Worktree '{wt.name}' (id {wt.id})",
        f"  project    : {project.name if project else wt.project_id}",
        f"  branch     : {wt.branch}",
        f"  base_branch: {wt.base_branch}",
        f"  path       : {wt.path}",
        f"  status     : {wt.status}",
        f"  created_at : {wt.created_at}",
        f"  updated_at : {wt.updated_at}",
    ]
    print("\n".join(detail))
    return EXIT_OK


def cmd_worktree_archive(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    wt = storage.get_worktree_by_name(db_path, args.name)
    if wt is None:
        print(f"error: worktree '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    storage.update_worktree_status(db_path, wt.id, worktrees.WORKTREE_ARCHIVED)
    print(f"Archived worktree '{wt.name}'. Files on disk were not removed.")
    return EXIT_OK


def cmd_worktree_remove(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    wt = storage.get_worktree_by_name(db_path, args.name)
    if wt is None:
        print(f"error: worktree '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    if not args.force and storage.count_active_runs_for_workspace(db_path, wt.path) > 0:
        print(f"error: worktree '{wt.name}' has an active run; pass --force to remove anyway", file=sys.stderr)
        return EXIT_USAGE
    project = storage.get_project_by_id(db_path, wt.project_id)
    if project is None or not project.repo_path:
        print(f"error: cannot resolve the repository for worktree '{wt.name}'", file=sys.stderr)
        return EXIT_USAGE
    try:
        worktrees.remove_git_worktree(project.repo_path, wt.path, force=args.force)
    except worktrees.WorktreeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_RUN_FAILED
    storage.delete_worktree_record(db_path, wt.id)
    print(f"Removed worktree '{wt.name}' (git worktree remove). Run records were kept.")
    return EXIT_OK


def _dispatch_worktree(args: argparse.Namespace) -> int:
    handlers = {
        "create": cmd_worktree_create,
        "list": cmd_worktree_list,
        "show": cmd_worktree_show,
        "archive": cmd_worktree_archive,
        "remove": cmd_worktree_remove,
    }
    handler = handlers.get(getattr(args, "worktree_command", None))
    if handler is None:
        print(
            "error: worktree requires a subcommand: create, list, show, archive, remove",
            file=sys.stderr,
        )
        return EXIT_USAGE
    return handler(args)


# -- locks commands ----------------------------------------------------------


def cmd_locks_list(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    locks.expire_locks(db_path)  # reflect any TTL expiry before listing
    items = storage.list_locks(db_path, limit=args.limit)
    if not items:
        print("No locks.")
        return EXIT_OK
    print(f"{'ID':>4}  {'RUN':>5}  {'STATUS':<9}  {'EXPIRES_AT':<32}  WORKSPACE")
    for lock in items:
        print(
            f"{lock.id:>4}  {lock.run_id:>5}  {lock.status:<9}  "
            f"{(lock.expires_at or '(none)'):<32}  {lock.workspace_path}"
        )
    return EXIT_OK


def cmd_locks_release(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    released = locks.release_lock(db_path, args.run_id)
    if released:
        print(f"Released {released} active lock(s) for run {args.run_id}.")
    else:
        print(f"No active lock to release for run {args.run_id}.")
    return EXIT_OK


def _dispatch_locks(args: argparse.Namespace) -> int:
    handlers = {"list": cmd_locks_list, "release": cmd_locks_release}
    handler = handlers.get(getattr(args, "locks_command", None))
    if handler is None:
        print("error: locks requires a subcommand: list, release", file=sys.stderr)
        return EXIT_USAGE
    return handler(args)


# -- queue + worker commands -------------------------------------------------


def cmd_queue_list(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    jobs = storage.list_queue(db_path, limit=args.limit)
    if not jobs:
        print("No queue jobs.")
        return EXIT_OK
    print(f"{'JOB':>4}  {'RUN':>5}  {'STATUS':<10}  {'PRIO':>4}  {'ATTEMPTS':<9}  {'CREATED_AT':<32}  ERROR")
    for job in jobs:
        attempts = f"{job.attempts}/{job.max_attempts}"
        error = _shorten(job.last_error, 40) if job.last_error else ""
        print(
            f"{job.id:>4}  {job.run_id:>5}  {job.status:<10}  {job.priority:>4}  {attempts:<9}  "
            f"{(job.created_at or ''):<32}  {error}"
        )
    return EXIT_OK


def _cancel_and_report(args: argparse.Namespace, default_reason: str) -> int:
    """Cancel a run via the cancellation service and print a compact result."""
    service = RunService(args.db_path)
    try:
        result = service.cancel_run(args.run_id, reason=getattr(args, "reason", None) or default_reason)
    except RunServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND if exc.kind == "not_found" else EXIT_USAGE
    note = "" if result.terminated else " (no local agent process was terminated)"
    print(f"Cancelled run {result.run_id} -> {result.run_status}.{note}")
    return EXIT_OK


def cmd_run_cancel(args: argparse.Namespace) -> int:
    return _cancel_and_report(args, default_reason="cancelled via CLI")


def cmd_queue_cancel(args: argparse.Namespace) -> int:
    return _cancel_and_report(args, default_reason="cancelled via queue cancel")


def _dispatch_queue(args: argparse.Namespace) -> int:
    handlers = {"list": cmd_queue_list, "cancel": cmd_queue_cancel}
    handler = handlers.get(getattr(args, "queue_command", None))
    if handler is None:
        print("error: queue requires a subcommand: list, cancel", file=sys.stderr)
        return EXIT_USAGE
    return handler(args)


def cmd_worker_run(args: argparse.Namespace) -> int:
    app_settings = settings.load_settings(args.config)
    db_path = storage.init_db(args.db_path or app_settings.storage.db_path)
    poll = (
        args.poll_interval_seconds
        if args.poll_interval_seconds is not None
        else app_settings.queue.poll_interval_seconds
    )
    local_worker = worker.LocalWorker(
        db_path, poll_interval_seconds=poll, reconcile_on_start=getattr(args, "reconcile_on_start", True)
    )
    if args.once:
        # run_forever(stop_after=1) so a one-shot worker still reconciles + heartbeats.
        executed = local_worker.run_forever(stop_after=1)
        print("worker: executed one job." if executed else "worker: no queued jobs.")
        return EXIT_OK
    print(f"worker: polling every {poll}s (Ctrl+C to stop)...")
    try:
        local_worker.run_forever()
    except KeyboardInterrupt:
        local_worker.stop()
        print("\nworker: stopped.")
    return EXIT_OK


def _dispatch_worker(args: argparse.Namespace) -> int:
    if getattr(args, "worker_command", None) == "run":
        return cmd_worker_run(args)
    print("error: worker requires a subcommand: run", file=sys.stderr)
    return EXIT_USAGE


# -- config commands ---------------------------------------------------------


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_config_toml(app_settings) -> str:
    """Render an AppSettings into a TOML document (built-in defaults for ``config init``)."""
    lines = []
    for section, values in settings.settings_to_dict(app_settings).items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def cmd_config_show(args: argparse.Namespace) -> int:
    try:
        app_settings = settings.load_settings(args.config)
    except settings.SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    for section, values in settings.settings_to_dict(app_settings).items():
        print(f"[{section}]")
        for key, value in values.items():
            # Never print the configured API token; show only that it is set or unset.
            if section == "auth" and key == "api_token":
                value = auth.redact_token(value)
            print(f"  {key} = {value}")
    return EXIT_OK


def cmd_config_validate(args: argparse.Namespace) -> int:
    try:
        settings.validate_settings(settings.load_settings(args.config))
    except settings.SettingsError as exc:
        print(f"error: invalid config: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print("Config is valid.")
    return EXIT_OK


def cmd_config_init(args: argparse.Namespace) -> int:
    target = os.path.join(".autoprompt", "config.toml")
    if os.path.exists(target) and not args.force:
        print(f"error: {target} already exists (use --force to overwrite)", file=sys.stderr)
        return EXIT_USAGE
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(_render_config_toml(settings.build_default_settings()))
    print(f"Created {target}")
    return EXIT_OK


def _dispatch_config(args: argparse.Namespace) -> int:
    handlers = {"show": cmd_config_show, "validate": cmd_config_validate, "init": cmd_config_init}
    handler = handlers.get(getattr(args, "config_command", None))
    if handler is None:
        print("error: config requires a subcommand: show, validate, init", file=sys.stderr)
        return EXIT_USAGE
    return handler(args)


# -- auth commands -----------------------------------------------------------


def cmd_auth_token_generate(args: argparse.Namespace) -> int:
    token = auth.generate_api_token()
    print(token)
    print("", file=sys.stderr)
    print("Store this token (it is not saved automatically). Either:", file=sys.stderr)
    print("  export AUTOPROMPT_API_TOKEN='<token>'   # and set AUTOPROMPT_AUTH_ENABLED=true", file=sys.stderr)
    print("  or set [auth] api_token / enabled in .autoprompt/config.toml", file=sys.stderr)
    return EXIT_OK


def _dispatch_auth(args: argparse.Namespace) -> int:
    if getattr(args, "auth_command", None) == "token" and getattr(args, "token_command", None) == "generate":
        return cmd_auth_token_generate(args)
    print("error: auth requires a subcommand: token generate", file=sys.stderr)
    return EXIT_USAGE


# -- system (stale-state reconciliation) -------------------------------------


def cmd_system_status(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    status = reconcile.build_system_status(db_path)
    print(f"Workers: {status.active_workers} active, {status.stale_workers} stale")
    print(f"Queue:   {status.queued_jobs} queued, {status.running_jobs} running")
    print(f"Locks:   {status.active_locks} active, {status.stale_locks} stale")
    print(f"Runs:    {status.stale_runs} stale RUNNING")
    return EXIT_OK


def cmd_system_reconcile(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    report = reconcile.reconcile_stale_state(db_path, dry_run=args.dry_run)
    label = "dry-run" if report.dry_run else "applied"
    print(
        f"Reconciliation ({label}): {report.stale_runs} run(s), {report.stale_queue_jobs} job(s), "
        f"{report.stale_locks} lock(s), {report.orphaned_cancellations} cancellation(s), "
        f"{report.stale_workers} worker(s)"
    )
    for action in report.actions:
        run = f" run #{action.run_id}" if action.run_id else ""
        print(f"  [{action.kind}] #{action.target_id}{run}: {action.action} -- {action.reason}")
    return EXIT_OK


def _dispatch_system(args: argparse.Namespace) -> int:
    handlers = {"status": cmd_system_status, "reconcile": cmd_system_reconcile}
    handler = handlers.get(getattr(args, "system_command", None))
    if handler is None:
        print("error: system requires a subcommand: status, reconcile", file=sys.stderr)
        return EXIT_USAGE
    return handler(args)


# -- search commands ---------------------------------------------------------


def cmd_search_runs(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    results = search.search_runs(
        db_path, query=args.query, status=args.status, provider=args.provider,
        limit=args.limit, offset=args.offset,
    )
    if not results:
        print("No matching runs.")
        return EXIT_OK
    print(f"{'ID':>4}  {'STATUS':<16}  {'PROVIDER':<12}  {'CREATED_AT':<32}  PROMPT")
    for r in results:
        print(
            f"{r.id:>4}  {r.status:<16}  {r.provider:<12}  {(r.created_at or ''):<32}  "
            f"{_shorten(r.prompt_preview, 50)}"
        )
    return EXIT_OK


def cmd_search_artifacts(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    results = search.search_artifacts(
        db_path, query=args.query, artifact_type=args.type, limit=args.limit, offset=args.offset,
    )
    if not results:
        print("No matching artifacts.")
        return EXIT_OK
    print(f"{'ID':>5}  {'RUN':>4}  {'STEP':>4}  {'TYPE':<18}  {'CREATED_AT':<32}  PREVIEW")
    for a in results:
        step = str(a.step_id) if a.step_id is not None else "-"
        print(
            f"{a.id:>5}  {a.run_id:>4}  {step:>4}  {a.type:<18}  {(a.created_at or ''):<32}  "
            f"{_shorten(a.match_preview, 50)}"
        )
    return EXIT_OK


def cmd_search_all(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    result = search.search_all(db_path, query=args.query, limit=args.limit, offset=args.offset)
    print(f"Runs ({len(result.runs)}):")
    for r in result.runs:
        print(f"  #{r.id} {r.status} {r.provider} -- {_shorten(r.prompt_preview, 60)}")
    print(f"Steps ({len(result.steps)}):")
    for s in result.steps:
        print(f"  run #{s.run_id} step #{s.loop_index} [{s.match_field}] -- {_shorten(s.match_preview, 60)}")
    print(f"Artifacts ({len(result.artifacts)}):")
    for a in result.artifacts:
        print(f"  #{a.id} run #{a.run_id} {a.type} [{a.match_field}] -- {_shorten(a.match_preview, 60)}")
    return EXIT_OK


def _dispatch_search(args: argparse.Namespace) -> int:
    handlers = {"runs": cmd_search_runs, "artifacts": cmd_search_artifacts, "all": cmd_search_all}
    handler = handlers.get(getattr(args, "search_command", None))
    if handler is None:
        print("error: search requires a subcommand: runs, artifacts, all", file=sys.stderr)
        return EXIT_USAGE
    return handler(args)


# -- compare commands --------------------------------------------------------


def _fmt_file_list(paths, limit: int = 50) -> str:
    if not paths:
        return "(none)"
    shown = ", ".join(paths[:limit])
    extra = len(paths) - limit
    return shown + (f", … (+{extra} more)" if extra > 0 else "")


def cmd_compare_runs(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    try:
        result = compare.compare_runs(
            db_path, args.run_a, args.run_b,
            show_prompts=args.show_prompts, show_artifacts=args.show_artifacts,
        )
    except compare.CompareError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND if exc.kind == "not_found" else EXIT_USAGE

    a, b = result.run_a, result.run_b
    same = lambda flag: "same" if flag else "differ"  # noqa: E731
    print(f"Run #{a.id} vs Run #{b.id}")
    print(f"  status:     {a.status} | {b.status}  ({same(result.same_status)})")
    print(f"  provider:   {a.provider} | {b.provider}  ({same(result.same_provider)})")
    print(f"  created_at: {a.created_at} | {b.created_at}")
    print(f"  steps:      {result.steps.step_count_a} | {result.steps.step_count_b}")
    print(f"  failed:     {result.steps.failed_steps_a} | {result.steps.failed_steps_b}")
    print(f"  exit codes: {result.steps.exit_codes_a} | {result.steps.exit_codes_b}")

    cf = result.changed_files
    print("Changed files:")
    print(f"  only A ({len(cf.only_a)}): {_fmt_file_list(cf.only_a)}")
    print(f"  only B ({len(cf.only_b)}): {_fmt_file_list(cf.only_b)}")
    print(f"  common ({len(cf.common)}): {_fmt_file_list(cf.common)}")
    if cf.warning:
        print(f"  warning: {cf.warning}")

    print(f"  diff stat A: {_shorten(result.diff_stat_a, 60) or '(none)'}")
    print(f"  diff stat B: {_shorten(result.diff_stat_b, 60) or '(none)'}")

    if args.show_prompts:
        print(f"Root prompt A: {a.root_prompt or '(none)'}")
        print(f"Root prompt B: {b.root_prompt or '(none)'}")
        print(f"Latest next prompt A: {result.latest_next_prompt_full_a or '(none)'}")
        print(f"Latest next prompt B: {result.latest_next_prompt_full_b or '(none)'}")
    else:
        print(f"Latest next prompt A: {result.latest_next_prompt_a or '(none)'}")
        print(f"Latest next prompt B: {result.latest_next_prompt_b or '(none)'}")

    if args.show_artifacts:
        counts_a = result.artifact_counts_by_type_a.counts
        counts_b = result.artifact_counts_by_type_b.counts
        print("Artifact counts by type:")
        print(f"  A: {_fmt_counts(counts_a)}")
        print(f"  B: {_fmt_counts(counts_b)}")

    print(f"Summary: {result.summary}")
    return EXIT_OK


def _fmt_counts(counts) -> str:
    if not counts:
        return "(none)"
    return ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))


def _dispatch_compare(args: argparse.Namespace) -> int:
    if getattr(args, "compare_command", None) == "runs":
        return cmd_compare_runs(args)
    print("error: compare requires a subcommand: runs", file=sys.stderr)
    return EXIT_USAGE


# -- chain commands ----------------------------------------------------------


def cmd_chain_show(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    try:
        chain = chains.build_prompt_chain(
            db_path, args.run_id,
            full_prompts=args.full_prompts, include_artifacts=args.artifacts, errors_only=args.errors_only,
        )
    except chains.ChainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND

    pending = " -- pending approval" if chain.pending_approval else ""
    print(
        f"Chain for run #{chain.run_id} ({chain.provider}, {chain.run_status}) -- "
        f"{chain.step_count} step(s), {chain.approval_count} approval(s), "
        f"{chain.failed_step_count} failed, {chain.total_artifact_count} artifact(s){pending}"
    )
    if not chain.chain_nodes:
        print("  (no failed nodes)" if args.errors_only else "  (no steps yet)")
        return EXIT_OK

    for node in chain.chain_nodes:
        exit_str = str(node.exit_code) if node.exit_code is not None else "-"
        approval = node.approval_status or "-"
        print(
            f"  [loop {node.loop_index}] step #{node.step_id}  {node.status:<8} "
            f"exit {exit_str:<4} approval: {approval}"
        )
        if args.full_prompts:
            print(f"    prompt:      {node.prompt or '(none)'}")
            print(f"    next prompt: {node.next_prompt or '(none)'}")
        else:
            print(f"    prompt:      {node.prompt_preview or '(none)'}")
            print(f"    next prompt: {node.next_prompt_preview or '(none)'}")
        if node.stderr_preview:
            print(f"    stderr:      {node.stderr_preview}")
        if args.artifacts:
            print(f"    artifacts:   {_fmt_counts(node.artifact_counts_by_type.counts)}")
            if node.changed_files_preview:
                print(f"    changed:     {_fmt_file_list(node.changed_files_preview)}")
    return EXIT_OK


def _dispatch_chain(args: argparse.Namespace) -> int:
    if getattr(args, "chain_command", None) == "show":
        return cmd_chain_show(args)
    print("error: chain requires a subcommand: show", file=sys.stderr)
    return EXIT_USAGE


# -- provider commands -------------------------------------------------------


def _provider_avail(profile) -> str:
    return "available" if providers.check_provider_available(profile) else "unavailable"


def cmd_provider_seed(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    result = providers.seed_default_provider_profiles(db_path, force=args.force)
    print(f"Provider profiles: {result['seeded']} seeded, {result['skipped']} skipped, {result['total']} total.")
    return EXIT_OK


def cmd_provider_list(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    profiles = storage.list_provider_profiles(db_path)
    if not profiles:
        print("No provider profiles. Run 'provider seed' to create the defaults.")
        return EXIT_OK
    print(f"{'NAME':<16}  {'TYPE':<12}  {'COMMAND':<12}  {'ENABLED':<7}  {'TIMEOUT':>7}  AVAILABILITY")
    for p in profiles:
        print(
            f"{p.name:<16}  {p.type:<12}  {p.command:<12}  {('yes' if p.enabled else 'no'):<7}  "
            f"{p.default_timeout_seconds:>7}  {_provider_avail(p)}"
        )
    return EXIT_OK


def cmd_provider_show(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    profile = storage.get_provider_profile_by_name(db_path, args.name)
    if profile is None:
        print(f"error: provider profile '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    print(f"Name:        {profile.name}")
    print(f"Type:        {profile.type}")
    print(f"Command:     {profile.command}")
    print(f"Timeout:     {profile.default_timeout_seconds}s")
    print(f"Default args:{' ' + profile.default_args if profile.default_args else ' (none)'}")
    print(f"Enabled:     {'yes' if profile.enabled else 'no'}")
    print(f"Availability:{' ' + _provider_avail(profile)}")
    print(f"Created:     {profile.created_at}")
    print(f"Updated:     {profile.updated_at}")
    return EXIT_OK


def cmd_provider_add(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    if storage.get_provider_profile_by_name(db_path, args.name) is not None:
        print(f"error: provider profile '{args.name}' already exists", file=sys.stderr)
        return EXIT_USAGE
    try:
        provider_type = providers.validate_provider_type(args.type)
        command = providers.validate_provider_command(args.command_exec)
        timeout = providers.validate_provider_timeout(args.timeout_seconds)
    except providers.ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    storage.create_provider_profile(
        db_path, name=args.name, type=provider_type, command=command,
        default_timeout_seconds=timeout, default_args=args.default_args, enabled=not args.disabled,
    )
    print(f"Created provider profile '{args.name}' ({provider_type}).")
    return EXIT_OK


def cmd_provider_update(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    profile = storage.get_provider_profile_by_name(db_path, args.name)
    if profile is None:
        print(f"error: provider profile '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    try:
        new_type = providers.validate_provider_type(args.type) if args.type is not None else None
        new_command = (
            providers.validate_provider_command(args.command_exec) if args.command_exec is not None else None
        )
        new_timeout = (
            providers.validate_provider_timeout(args.timeout_seconds) if args.timeout_seconds is not None else None
        )
    except providers.ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    storage.update_provider_profile(
        db_path, profile.id, type=new_type, command=new_command,
        default_timeout_seconds=new_timeout,
        default_args=args.default_args if args.default_args is not None else storage._UNSET,
    )
    print(f"Updated provider profile '{args.name}'.")
    return EXIT_OK


def cmd_provider_enable(args: argparse.Namespace) -> int:
    return _provider_set_enabled(args, True)


def cmd_provider_disable(args: argparse.Namespace) -> int:
    return _provider_set_enabled(args, False)


def _provider_set_enabled(args: argparse.Namespace, enabled: bool) -> int:
    db_path = storage.init_db(args.db_path)
    profile = storage.get_provider_profile_by_name(db_path, args.name)
    if profile is None:
        print(f"error: provider profile '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    storage.set_provider_enabled(db_path, profile.id, enabled)
    print(f"Provider profile '{args.name}' {'enabled' if enabled else 'disabled'}.")
    return EXIT_OK


def cmd_provider_delete(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    profile = storage.get_provider_profile_by_name(db_path, args.name)
    if profile is None:
        print(f"error: provider profile '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    storage.delete_provider_profile(db_path, profile.id)
    print(f"Deleted provider profile '{args.name}'. (No external CLI tool was removed.)")
    return EXIT_OK


def cmd_provider_check(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    profile = storage.get_provider_profile_by_name(db_path, args.name)
    if profile is None:
        print(f"error: provider profile '{args.name}' not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    available = providers.check_provider_available(profile)
    status = "available" if available else "unavailable"
    print(f"{profile.name} ({profile.type}): command '{profile.command}' is {status}.")
    return EXIT_OK if available else EXIT_RUN_FAILED


def _dispatch_provider(args: argparse.Namespace) -> int:
    handlers = {
        "seed": cmd_provider_seed,
        "list": cmd_provider_list,
        "show": cmd_provider_show,
        "add": cmd_provider_add,
        "update": cmd_provider_update,
        "enable": cmd_provider_enable,
        "disable": cmd_provider_disable,
        "delete": cmd_provider_delete,
        "check": cmd_provider_check,
    }
    handler = handlers.get(getattr(args, "provider_command", None))
    if handler is None:
        print(
            "error: provider requires a subcommand: seed, list, show, add, update, enable, disable, delete, check",
            file=sys.stderr,
        )
        return EXIT_USAGE
    return handler(args)


# -- recovery commands -------------------------------------------------------


def _recovery_exit(kind: str) -> int:
    return EXIT_NOT_FOUND if kind == "not_found" else EXIT_RUN_FAILED


def cmd_recovery_propose(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    try:
        attempt = recovery.propose_recovery(db_path, args.run_id, reason=args.reason)
    except recovery.RecoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _recovery_exit(exc.kind)
    print(f"Recovery #{attempt.id} proposed for run #{attempt.source_run_id} (status {attempt.status}).")
    if args.show_prompt:
        print(attempt.recovery_prompt)
    else:
        print(f"  prompt: {_shorten(attempt.recovery_prompt, 120)}")
    return EXIT_OK


def cmd_recovery_approve(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    try:
        attempt = recovery.approve_recovery(db_path, args.recovery_id)
    except recovery.RecoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _recovery_exit(exc.kind)
    print(f"Recovery #{attempt.id} approved.")
    if args.execute:
        try:
            result = recovery.execute_recovery(db_path, args.recovery_id, queued=args.queued)
        except recovery.RecoveryError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return _recovery_exit(exc.kind)
        _print_recovery_execution(result)
    return EXIT_OK


def cmd_recovery_reject(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    try:
        attempt = recovery.reject_recovery(db_path, args.recovery_id, reason=args.reason)
    except recovery.RecoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _recovery_exit(exc.kind)
    print(f"Recovery #{attempt.id} rejected.")
    return EXIT_OK


def cmd_recovery_execute(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    try:
        result = recovery.execute_recovery(db_path, args.recovery_id, queued=args.queued)
    except recovery.RecoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _recovery_exit(exc.kind)
    _print_recovery_execution(result)
    return EXIT_OK


def _print_recovery_execution(result) -> None:
    state = "queued" if result.queued else (result.run_status or "?")
    print(f"Recovery #{result.attempt.id} executed -> run #{result.recovery_run_id} ({state}).")
    if result.error:
        print(f"  note: recovery run did not start cleanly: {result.error}")


def cmd_recovery_list(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    items = (
        recovery.list_recoveries_for_run(db_path, args.run_id)
        if args.run_id is not None
        else recovery.list_recoveries(db_path)
    )
    if not items:
        print("No recovery attempts.")
        return EXIT_OK
    print(f"{'ID':>4}  {'SOURCE':>6}  {'RUN':>6}  {'STATUS':<10}  CREATED_AT")
    for a in items:
        run = str(a.recovery_run_id) if a.recovery_run_id is not None else "-"
        print(f"{a.id:>4}  {a.source_run_id:>6}  {run:>6}  {a.status:<10}  {a.created_at}")
    return EXIT_OK


def _dispatch_recovery(args: argparse.Namespace) -> int:
    handlers = {
        "propose": cmd_recovery_propose,
        "approve": cmd_recovery_approve,
        "reject": cmd_recovery_reject,
        "execute": cmd_recovery_execute,
        "list": cmd_recovery_list,
    }
    handler = handlers.get(getattr(args, "recovery_command", None))
    if handler is None:
        print("error: recovery requires a subcommand: propose, approve, reject, execute, list", file=sys.stderr)
        return EXIT_USAGE
    return handler(args)


# -- checkpoint commands -----------------------------------------------------


def _checkpoint_exit(kind: str) -> int:
    if kind == "not_found":
        return EXIT_NOT_FOUND
    if kind == "not_confirmed":
        return EXIT_USAGE
    return EXIT_RUN_FAILED  # "unsafe" and any other refusal


def cmd_checkpoint_list(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    items = checkpoints.list_checkpoints(db_path, args.run_id)
    if not items:
        print(f"No checkpoints for run #{args.run_id}.")
        return EXIT_OK
    print(f"{'ID':>4}  {'STEP':>4}  {'STATUS':<9}  {'HEAD':<12}  {'BRANCH':<16}  {'DIRTY':<5}  WORKSPACE")
    for cp in items:
        head = (cp.git_head_before or "-")[:12]
        branch = (cp.git_branch_before or "-")[:16]
        step = str(cp.step_id) if cp.step_id is not None else "-"
        dirty = "yes" if checkpoints.detect_preexisting_dirty_state(cp) else "no"
        print(f"{cp.id:>4}  {step:>4}  {cp.status:<9}  {head:<12}  {branch:<16}  {dirty:<5}  {cp.workspace_path}")
    return EXIT_OK


def cmd_checkpoint_show(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    cp = checkpoints.get_checkpoint(db_path, args.checkpoint_id)
    if cp is None:
        print(f"error: checkpoint {args.checkpoint_id} not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    print(f"Checkpoint #{cp.id} (run #{cp.run_id}) [{cp.status}]")
    print(f"  workspace: {cp.workspace_path}")
    print(f"  head:      {cp.git_head_before or '-'}")
    print(f"  branch:    {cp.git_branch_before or '-'}")
    print(f"  created:   {cp.created_at}")
    if checkpoints.detect_preexisting_dirty_state(cp):
        print("  warning:   workspace had uncommitted changes before the run (dirty)")
    if cp.restored_at:
        print(f"  restored:  {cp.restored_at}")
    if cp.restore_error:
        print(f"  note:      {cp.restore_error}")
    try:
        plan = checkpoints.build_rollback_plan(db_path, cp.id)
    except checkpoints.CheckpointError as exc:
        print(f"  plan:      unavailable ({exc})")
        return EXIT_OK
    _print_rollback_plan(plan)
    return EXIT_OK


def cmd_checkpoint_rollback_plan(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    try:
        plan = checkpoints.build_rollback_plan(db_path, args.checkpoint_id)
    except checkpoints.CheckpointError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _checkpoint_exit(exc.kind)
    _print_rollback_plan(plan)
    return EXIT_OK


def _print_rollback_plan(plan) -> None:
    print(f"Rollback plan for checkpoint #{plan.checkpoint_id} (run #{plan.run_id}):")
    print(f"  {plan.summary}")
    print(f"  target HEAD:  {plan.target_head or '-'}  (branch {plan.target_branch or '-'})")
    print(f"  current HEAD: {plan.current_head or '-'}  (branch {plan.current_branch or '-'})")
    print(f"  can rollback: {plan.can_rollback}   safe: {plan.safe}   requires --force: {plan.requires_force}")
    for warning in plan.warnings:
        print(f"  warning: {warning}")


def cmd_checkpoint_rollback(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    if not args.confirm:
        print(
            "error: rollback requires --confirm (it runs git reset --hard and discards uncommitted changes)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        result = checkpoints.rollback_checkpoint(db_path, args.checkpoint_id, confirm=True, force=args.force)
    except checkpoints.CheckpointError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _checkpoint_exit(exc.kind)
    if result.restored:
        print(
            f"Checkpoint #{result.checkpoint_id} rolled back: {result.message}. "
            f"HEAD is now {result.git_head_after}."
        )
        return EXIT_OK
    print(f"error: rollback failed: {result.error}", file=sys.stderr)
    return EXIT_RUN_FAILED


def _dispatch_checkpoint(args: argparse.Namespace) -> int:
    handlers = {
        "list": cmd_checkpoint_list,
        "show": cmd_checkpoint_show,
        "rollback-plan": cmd_checkpoint_rollback_plan,
        "rollback": cmd_checkpoint_rollback,
    }
    handler = handlers.get(getattr(args, "checkpoint_command", None))
    if handler is None:
        print("error: checkpoint requires a subcommand: list, show, rollback-plan, rollback", file=sys.stderr)
        return EXIT_USAGE
    return handler(args)


# -- export / import commands ------------------------------------------------


def _print_export_summary(summary: dict) -> None:
    counts = summary.get("counts", {})
    print(
        f"  format {summary.get('format')} v{summary.get('version')}  "
        f"redacted={summary.get('redacted')} ({summary.get('redacted_artifacts', 0)} artifact(s))"
    )
    print("  " + ", ".join(f"{name}={counts[name]}" for name in counts))


def cmd_export_data(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    payload = export_import.build_export_payload(
        db_path,
        include_projects=args.include_projects,
        include_providers=args.include_providers,
        include_templates=args.include_templates,
        include_runs=True,
        include_artifacts=args.include_artifacts,
        include_recoveries=args.include_recoveries,
        run_ids=args.run_id,
        project_names=args.project,
        artifact_content=args.artifact_content,
        redact_sensitive=args.redact_sensitive,
    )
    export_import.write_export_file(args.output, payload)
    print(f"Exported to {args.output}")
    _print_export_summary(export_import.summarize_export(payload))
    return EXIT_OK


def cmd_export_summary(args: argparse.Namespace) -> int:
    try:
        payload = export_import.read_export_file(args.input)
    except export_import.ExportImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_RUN_FAILED
    print(f"Export file: {args.input}")
    _print_export_summary(export_import.summarize_export(payload))
    try:
        export_import.validate_export_payload(payload)
    except export_import.ExportImportError as exc:
        print(f"  warning: not importable: {exc}")
    return EXIT_OK


def cmd_import_data(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    try:
        payload = export_import.read_export_file(args.input)
        result = export_import.import_export_payload(db_path, payload, mode=args.mode)
    except export_import.ExportImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_RUN_FAILED
    print(f"Imported (mode {result['mode']}): {result['imported']} row(s), {result['skipped']} skipped.")
    for name, entity in result["entities"].items():
        if entity["imported"] or entity["skipped"]:
            print(f"  {name}: {entity['imported']} imported, {entity['skipped']} skipped")
    return EXIT_OK


def _dispatch_export(args: argparse.Namespace) -> int:
    handlers = {"data": cmd_export_data, "summary": cmd_export_summary}
    handler = handlers.get(getattr(args, "export_command", None))
    if handler is None:
        print("error: export requires a subcommand: data, summary", file=sys.stderr)
        return EXIT_USAGE
    return handler(args)


def _dispatch_import(args: argparse.Namespace) -> int:
    if getattr(args, "import_command", None) == "data":
        return cmd_import_data(args)
    print("error: import requires a subcommand: data", file=sys.stderr)
    return EXIT_USAGE


# -- run + run history -------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    try:
        db_path = args.db_path or settings.load_settings(args.config).storage.db_path
        prompt, run_settings = resolve_run_inputs(
            db_path,
            prompt=args.prompt,
            project=args.project,
            provider=args.provider,
            workspace=args.workspace,
            max_loops=args.max_loops,
            timeout_seconds=args.timeout_seconds,
            no_approval=args.no_approval,
            template=args.template,
            goal=args.goal,
            extra_context=args.extra_context,
            worktree=args.worktree,
            config_path=args.config,
        )
    except settings.SettingsError as exc:
        print(f"error: invalid config: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except RunInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND if exc.kind == "not_found" else EXIT_USAGE

    service = RunService(db_path)
    if args.queued:
        try:
            run_id = service.create_run_only(
                prompt=prompt,
                provider=run_settings.provider,
                max_loops=run_settings.max_loops,
                require_approval=run_settings.require_approval,
                workspace=run_settings.workspace,
                timeout_seconds=run_settings.timeout_seconds,
            )
            job_id = queue.enqueue(db_path, run_id)
        except (RunServiceError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        print(f"Queued run {run_id} as job {job_id}. Run a worker to execute it: worker run")
        return EXIT_OK

    try:
        report = service.create_and_execute_run(
            prompt=prompt,
            provider=run_settings.provider,
            max_loops=run_settings.max_loops,
            require_approval=run_settings.require_approval,
            workspace=run_settings.workspace,
            timeout_seconds=run_settings.timeout_seconds,
        )
    except (RunServiceError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    _print_step_report(report, show_next_prompt=args.show_next_prompt)
    _print_safety_warnings(db_path, report.run_id)
    return _exit_code_for(report)


def cmd_approve_next(args: argparse.Namespace) -> int:
    service = RunService(args.db_path)
    try:
        report = service.approve_and_continue(args.run_id)
    except (RunServiceError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    _print_step_report(report, show_next_prompt=args.show_next_prompt)
    return _exit_code_for(report)


def cmd_reject_next(args: argparse.Namespace) -> int:
    service = RunService(args.db_path)
    try:
        report = service.reject(args.run_id)
    except (RunServiceError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    _print_step_report(report)
    return EXIT_OK


def cmd_list_runs(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    runs = storage.list_runs(db_path, limit=args.limit)
    if not runs:
        print("No runs found.")
        return EXIT_OK
    print(f"{'ID':>4}  {'STATUS':<16}  {'PROVIDER':<12}  {'CREATED_AT':<32}  PROMPT")
    for run in runs:
        print(
            f"{run.id:>4}  {run.status:<16}  {run.provider:<12}  "
            f"{(run.created_at or ''):<32}  {_shorten(run.root_prompt, 40)}"
        )
    return EXIT_OK


def cmd_show_run(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    run = storage.get_run(db_path, args.run_id)
    if run is None:
        print(f"error: run {args.run_id} not found", file=sys.stderr)
        return EXIT_NOT_FOUND

    steps = storage.get_steps_for_run(db_path, run.id)
    pending = storage.get_pending_approval(db_path, run.id)
    detail = [
        f"Run #{run.id}",
        f"  status          : {run.status}",
        f"  provider        : {run.provider}",
        f"  workspace       : {run.workspace or '(none)'}",
        f"  root_prompt     : {run.root_prompt}",
        f"  max_loops       : {run.max_loops}",
        f"  require_approval: {run.require_approval}",
        f"  created_at      : {run.created_at}",
        f"  finished_at     : {run.finished_at or '(none)'}",
    ]
    print("\n".join(detail))
    print(f"Steps ({len(steps)}):")
    if not steps:
        print("  (none)")
    for step in steps:
        print(
            f"  [{step.loop_index}] {step.status}  exit={step.exit_code}  "
            f"started={step.started_at}  finished={step.finished_at}"
        )
        print(f"      prompt     : {_shorten(step.prompt, 70)}")
        if step.stdout:
            print(f"      stdout     : {_shorten(step.stdout, 80)}")
        if step.stderr:
            print(f"      stderr     : {_shorten(step.stderr, 80)}")
        if step.next_prompt:
            print(f"      next_prompt: {_shorten(step.next_prompt, 80)}")
        _print_step_git_summary(db_path, step.id)

    if pending is not None:
        print("Pending approval:")
        print(f"  id          : {pending.id}")
        print(f"  step_id     : {pending.step_id}")
        print(f"  status      : {pending.status}")
        print(f"  next_prompt : {_shorten(pending.next_prompt, 100)}")
    else:
        last_next = next((s.next_prompt for s in reversed(steps) if s.next_prompt), None)
        if last_next:
            print(f"Next prompt : {_shorten(last_next, 100)}")

    cancellation = storage.get_cancellation_for_run(db_path, run.id)
    if cancellation is not None:
        print("Cancellation:")
        print(f"  status      : {cancellation.status}")
        print(f"  requested_at: {cancellation.requested_at}")
        if cancellation.reason:
            print(f"  reason      : {_shorten(cancellation.reason, 80)}")
        if cancellation.error:
            print(f"  error       : {_shorten(cancellation.error, 80)}")
        latest = storage.get_latest_artifact_by_type(db_path, run.id, cancel.CANCELLATION_ARTIFACT)
        if latest is not None and latest.content:
            print(f"  artifact    : {_shorten(latest.content, 80)}")
    return EXIT_OK


def cmd_show_artifacts(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    items = storage.list_artifacts_for_run(db_path, args.run_id, artifact_type=args.type)
    if not items:
        suffix = f" of type '{args.type}'" if args.type else ""
        print(f"No artifacts{suffix} for run {args.run_id}.")
        return EXIT_OK
    print(f"{'ID':>4}  {'STEP':>4}  {'TYPE':<18}  {'CREATED_AT':<32}  PREVIEW")
    for item in items:
        step = str(item.step_id) if item.step_id is not None else "-"
        preview = _shorten(item.content, 50) if item.content else ""
        print(f"{item.id:>4}  {step:>4}  {item.type:<18}  {(item.created_at or ''):<32}  {preview}")
    return EXIT_OK


def cmd_show_artifact(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    artifact = storage.get_artifact(db_path, args.artifact_id)
    if artifact is None:
        print(f"error: artifact {args.artifact_id} not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    step = artifact.step_id if artifact.step_id is not None else "(none)"
    print(f"Artifact #{artifact.id} (run {artifact.run_id}, step {step}, type {artifact.type})")
    print(f"  created_at: {artifact.created_at}")
    if artifact.path:
        print(f"  path      : {artifact.path}")
    print("---")
    print(artifact.content if artifact.content is not None else "")
    return EXIT_OK


# -- helpers -----------------------------------------------------------------


def _print_step_git_summary(db_path: str, step_id: int) -> None:
    """Print compact changed-files and diff-stat lines for a step, if captured."""
    by_type = {a.type: a for a in storage.list_artifacts_for_step(db_path, step_id)}
    changed = by_type.get(ArtifactType.CHANGED_FILES.value)
    if changed is not None and changed.content and changed.content.strip():
        files = ", ".join(changed.content.splitlines())
        print(f"      changed    : {_shorten(files, 80)}")
    diff_stat = by_type.get(ArtifactType.GIT_DIFF_STAT.value)
    if diff_stat is not None and diff_stat.content and diff_stat.content.strip():
        summary_lines = [line for line in diff_stat.content.splitlines() if line.strip()]
        if summary_lines:
            print(f"      diffstat   : {_shorten(summary_lines[-1].strip(), 80)}")


def _exit_code_for(report: StepExecutionReport) -> int:
    return EXIT_RUN_FAILED if report.run_status == RunStatus.FAILED.value else EXIT_OK


def _shorten(text: Optional[str], limit: int) -> str:
    """Collapse whitespace and truncate ``text`` to ``limit`` characters."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)] + "..."


def _print_safety_warnings(db_path: Optional[str], run_id: int) -> None:
    """Print compact safety-warning artifacts recorded for a run, if any."""
    warnings = storage.list_artifacts_for_run(db_path, run_id, artifact_type=safety.SAFETY_WARNING_ARTIFACT)
    if warnings:
        print("Safety warnings:")
        for warning in warnings:
            print(f"  - {_shorten(warning.content, 100)}")


def _print_step_report(report: StepExecutionReport, show_next_prompt: bool = False) -> None:
    """Print a compact report for a run step / advance.

    With ``show_next_prompt`` the full generated next prompt is printed after the
    compact report; otherwise only the truncated preview line is shown.
    """
    lines = [
        "Run report",
        f"  run_id      : {report.run_id}",
        f"  status      : {report.run_status}",
        f"  provider    : {report.provider}",
        f"  loop_index  : {report.loop_index}",
        f"  step_id     : {report.step_id if report.step_id is not None else '(none)'}",
        f"  exit_code   : {report.exit_code if report.exit_code is not None else '(none)'}",
        f"  approval_id : {report.approval_id if report.approval_id is not None else '(none)'}",
        f"  next_prompt : {_shorten(report.next_prompt, 100) if report.next_prompt else '(none)'}",
    ]
    if report.message:
        lines.append(f"  note        : {report.message}")
    print("\n".join(lines))
    if show_next_prompt and report.next_prompt:
        print("Next prompt (full):")
        print(report.next_prompt)


def _dispatch_project(args: argparse.Namespace) -> int:
    handlers = {
        "add": cmd_project_add,
        "list": cmd_project_list,
        "show": cmd_project_show,
        "set-default": cmd_project_set_default,
        "delete": cmd_project_delete,
    }
    handler = handlers.get(getattr(args, "project_command", None))
    if handler is None:
        print(
            "error: project requires a subcommand: add, list, show, set-default, delete",
            file=sys.stderr,
        )
        return EXIT_USAGE
    return handler(args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse ``argv`` (defaults to ``sys.argv``) and dispatch to a command handler."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        return cmd_version()
    if args.command == "init-db":
        return cmd_init_db(args)
    if args.command == "safety-check":
        return cmd_safety_check(args)
    if args.command == "project":
        return _dispatch_project(args)
    if args.command == "template":
        return _dispatch_template(args)
    if args.command == "worktree":
        return _dispatch_worktree(args)
    if args.command == "locks":
        return _dispatch_locks(args)
    if args.command == "queue":
        return _dispatch_queue(args)
    if args.command == "worker":
        return _dispatch_worker(args)
    if args.command == "config":
        return _dispatch_config(args)
    if args.command == "auth":
        return _dispatch_auth(args)
    if args.command == "system":
        return _dispatch_system(args)
    if args.command == "search":
        return _dispatch_search(args)
    if args.command == "compare":
        return _dispatch_compare(args)
    if args.command == "chain":
        return _dispatch_chain(args)
    if args.command == "provider":
        return _dispatch_provider(args)
    if args.command == "recovery":
        return _dispatch_recovery(args)
    if args.command == "checkpoint":
        return _dispatch_checkpoint(args)
    if args.command == "export":
        return _dispatch_export(args)
    if args.command == "import":
        return _dispatch_import(args)
    if args.command == "run":
        if getattr(args, "run_command", None) == "cancel":
            return cmd_run_cancel(args)
        return cmd_run(args)
    if args.command == "approve-next":
        return cmd_approve_next(args)
    if args.command == "reject-next":
        return cmd_reject_next(args)
    if args.command == "list-runs":
        return cmd_list_runs(args)
    if args.command == "show-run":
        return cmd_show_run(args)
    if args.command == "show-artifacts":
        return cmd_show_artifacts(args)
    if args.command == "show-artifact":
        return cmd_show_artifact(args)

    parser.print_help(sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
