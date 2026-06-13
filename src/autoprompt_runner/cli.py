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

from . import __version__, locks, queue, safety, storage, templates, worker, worktrees
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
        "--poll-interval-seconds", dest="poll_interval_seconds", type=float, default=2.0,
        help="Seconds to wait between polls when the queue is empty (default 2).",
    )
    _add_db_path(run_parser)


# -- simple commands ---------------------------------------------------------


def cmd_version() -> int:
    print(__version__)
    return EXIT_OK


def cmd_init_db(args: argparse.Namespace) -> int:
    path = storage.init_db(args.db_path)
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


def cmd_queue_cancel(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    result = queue.cancel(db_path, args.run_id)
    if result == queue.CANCEL_CANCELLED:
        print(f"Cancelled queued job for run {args.run_id}.")
        return EXIT_OK
    if result == queue.CANCEL_RUNNING:
        print(
            f"error: run {args.run_id} job is already running; process cancellation is not implemented yet",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if result == queue.CANCEL_NOT_FOUND:
        print(f"error: no queue job for run {args.run_id}", file=sys.stderr)
        return EXIT_NOT_FOUND
    print(f"error: run {args.run_id} job is not cancellable (already finished)", file=sys.stderr)
    return EXIT_USAGE


def _dispatch_queue(args: argparse.Namespace) -> int:
    handlers = {"list": cmd_queue_list, "cancel": cmd_queue_cancel}
    handler = handlers.get(getattr(args, "queue_command", None))
    if handler is None:
        print("error: queue requires a subcommand: list, cancel", file=sys.stderr)
        return EXIT_USAGE
    return handler(args)


def cmd_worker_run(args: argparse.Namespace) -> int:
    db_path = storage.init_db(args.db_path)
    local_worker = worker.LocalWorker(db_path, poll_interval_seconds=args.poll_interval_seconds)
    if args.once:
        executed = local_worker.run_once()
        print("worker: executed one job." if executed else "worker: no queued jobs.")
        return EXIT_OK
    print(f"worker: polling every {args.poll_interval_seconds}s (Ctrl+C to stop)...")
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


# -- run + run history -------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    try:
        prompt, settings = resolve_run_inputs(
            args.db_path,
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
        )
    except RunInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND if exc.kind == "not_found" else EXIT_USAGE

    service = RunService(args.db_path)
    if args.queued:
        try:
            run_id = service.create_run_only(
                prompt=prompt,
                provider=settings.provider,
                max_loops=settings.max_loops,
                require_approval=settings.require_approval,
                workspace=settings.workspace,
                timeout_seconds=settings.timeout_seconds,
            )
            job_id = queue.enqueue(args.db_path, run_id)
        except (RunServiceError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        print(f"Queued run {run_id} as job {job_id}. Run a worker to execute it: worker run")
        return EXIT_OK

    try:
        report = service.create_and_execute_run(
            prompt=prompt,
            provider=settings.provider,
            max_loops=settings.max_loops,
            require_approval=settings.require_approval,
            workspace=settings.workspace,
            timeout_seconds=settings.timeout_seconds,
        )
    except (RunServiceError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    _print_step_report(report, show_next_prompt=args.show_next_prompt)
    _print_safety_warnings(args.db_path, report.run_id)
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
    if args.command == "run":
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
