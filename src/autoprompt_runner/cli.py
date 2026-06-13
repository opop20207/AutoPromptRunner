"""Command-line interface for AutoPromptRunner.

CLI-first entry point (see PROJECT.md). Commands:

* ``version``        -- print the package version.
* ``init-db``        -- create the local SQLite database.
* ``project``        -- manage project profiles (add / list / show / set-default / delete).
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

from . import __version__, storage
from .artifacts import ArtifactType
from .models import StepExecutionReport
from .projects import resolve_run_settings
from .services.run_service import DEFAULT_PROVIDER_FACTORIES, RunService, RunServiceError
from .state import RunStatus

# Provider names the CLI accepts (resolution/construction lives in RunService).
SUPPORTED_PROVIDERS = tuple(sorted(DEFAULT_PROVIDER_FACTORIES))

# Providers that require a workspace directory to run.
WORKSPACE_REQUIRED_PROVIDERS = ("claude-code", "codex")

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

    _add_project_commands(subparsers)

    run_parser = subparsers.add_parser(
        "run", help="Start a run; pause at an approval gate by default."
    )
    run_parser.add_argument(
        "--project", default=None,
        help="Use settings from this project profile (defaults to the default project).",
    )
    run_parser.add_argument("--prompt", required=True, help="Prompt to send. Must not be empty.")
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
        "--timeout-seconds", dest="timeout_seconds", type=int, default=None,
        help="Subprocess timeout in seconds (>= 1). Overrides the project default.",
    )
    run_parser.add_argument(
        "--show-next-prompt", dest="show_next_prompt", action="store_true",
        help="Print the full generated next prompt instead of only a compact preview.",
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


# -- simple commands ---------------------------------------------------------


def cmd_version() -> int:
    print(__version__)
    return EXIT_OK


def cmd_init_db(args: argparse.Namespace) -> int:
    path = storage.init_db(args.db_path)
    print(f"Database initialized at: {path}")
    return EXIT_OK


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


# -- run + run history -------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    prompt = (args.prompt or "").strip()
    if not prompt:
        print("error: --prompt must not be empty", file=sys.stderr)
        return EXIT_USAGE

    db_path = storage.init_db(args.db_path)

    if args.project:
        project = storage.get_project_by_name(db_path, args.project)
        if project is None:
            print(f"error: project '{args.project}' not found", file=sys.stderr)
            return EXIT_NOT_FOUND
    else:
        project = storage.get_default_project(db_path)

    settings = resolve_run_settings(
        project,
        provider=args.provider,
        max_loops=args.max_loops,
        timeout_seconds=args.timeout_seconds,
        workspace=args.workspace,
        no_approval=args.no_approval,
    )

    if settings.provider not in DEFAULT_PROVIDER_FACTORIES:
        print(
            f"error: unsupported provider '{settings.provider}'. Supported: {', '.join(SUPPORTED_PROVIDERS)}",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if settings.max_loops < 1:
        print("error: --max-loops must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    if settings.timeout_seconds < 1:
        print("error: --timeout-seconds must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    if settings.provider in WORKSPACE_REQUIRED_PROVIDERS:
        if not settings.workspace:
            print(
                f"error: --workspace is required for the {settings.provider} provider "
                "(pass --workspace or use a project repo_path)",
                file=sys.stderr,
            )
            return EXIT_USAGE
        if not os.path.isdir(settings.workspace):
            print(f"error: workspace does not exist or is not a directory: {settings.workspace}", file=sys.stderr)
            return EXIT_USAGE

    service = RunService(db_path)
    try:
        report = service.start(
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
    if args.command == "project":
        return _dispatch_project(args)
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
