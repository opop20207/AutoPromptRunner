"""Command-line interface for AutoPromptRunner.

CLI-first entry point (see PROJECT.md). Commands in this step:

* ``version``      -- print the package version.
* ``init-db``      -- create the local SQLite database.
* ``run``          -- start a run; execute the first step, generate the next prompt,
  and pause at a pending approval (default) or auto-run up to ``--max-loops``.
* ``approve-next`` -- approve a run's pending next prompt and execute it.
* ``reject-next``  -- reject a run's pending next prompt and stop the run.
* ``list-runs``    -- list recent runs.
* ``show-run``     -- show one run, its steps, and any pending approval.

Real provider execution (Claude Code, Codex) and a web UI are not implemented yet.
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, Optional, Sequence, Type

from . import __version__, storage
from .runners import AgentRunner, MockRunner
from .models import StepExecutionReport
from .services import RunService, RunServiceError
from .state import RunStatus

# Registry of providers exposed by the CLI. Only the mock provider is supported here.
PROVIDERS: Dict[str, Type[AgentRunner]] = {"mock": MockRunner}

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

    run_parser = subparsers.add_parser(
        "run", help="Start a run; pause at an approval gate by default."
    )
    run_parser.add_argument("--prompt", required=True, help="Prompt to send. Must not be empty.")
    run_parser.add_argument(
        "--provider", default="mock", help="Provider to use. Only 'mock' is supported in this step."
    )
    run_parser.add_argument(
        "--max-loops", dest="max_loops", type=int, default=1, help="Max agent invocations. Must be >= 1."
    )
    run_parser.add_argument(
        "--no-approval", dest="no_approval", action="store_true",
        help="Disable the approval gate and auto-run up to --max-loops.",
    )
    _add_db_path(run_parser)

    approve_parser = subparsers.add_parser(
        "approve-next", help="Approve a run's pending next prompt and execute it."
    )
    approve_parser.add_argument("--run-id", dest="run_id", type=int, required=True, help="Run id.")
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

    return parser


def cmd_version() -> int:
    print(__version__)
    return EXIT_OK


def cmd_init_db(args: argparse.Namespace) -> int:
    path = storage.init_db(args.db_path)
    print(f"Database initialized at: {path}")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    prompt = (args.prompt or "").strip()
    if not prompt:
        print("error: --prompt must not be empty", file=sys.stderr)
        return EXIT_USAGE
    if args.max_loops < 1:
        print("error: --max-loops must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    if args.provider not in PROVIDERS:
        supported = ", ".join(sorted(PROVIDERS))
        print(f"error: unsupported provider '{args.provider}'. Supported: {supported}", file=sys.stderr)
        return EXIT_USAGE

    service = RunService(args.db_path)
    report = service.start(
        prompt=prompt,
        provider=args.provider,
        max_loops=args.max_loops,
        require_approval=not args.no_approval,
    )
    _print_step_report(report)
    return _exit_code_for(report)


def cmd_approve_next(args: argparse.Namespace) -> int:
    service = RunService(args.db_path)
    try:
        report = service.approve_and_continue(args.run_id)
    except RunServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    _print_step_report(report)
    return _exit_code_for(report)


def cmd_reject_next(args: argparse.Namespace) -> int:
    service = RunService(args.db_path)
    try:
        report = service.reject(args.run_id)
    except RunServiceError as exc:
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
    print(f"{'ID':>4}  {'STATUS':<16}  {'PROVIDER':<10}  {'CREATED_AT':<32}  PROMPT")
    for run in runs:
        print(
            f"{run.id:>4}  {run.status:<16}  {run.provider:<10}  "
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
        if step.next_prompt:
            print(f"      next_prompt: {_shorten(step.next_prompt, 80)}")

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


def _exit_code_for(report: StepExecutionReport) -> int:
    return EXIT_RUN_FAILED if report.run_status == RunStatus.FAILED.value else EXIT_OK


def _shorten(text: Optional[str], limit: int) -> str:
    """Collapse whitespace and truncate ``text`` to ``limit`` characters."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)] + "..."


def _print_step_report(report: StepExecutionReport) -> None:
    """Print a compact report for a run step / advance."""
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse ``argv`` (defaults to ``sys.argv``) and dispatch to a command handler."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        return cmd_version()
    if args.command == "init-db":
        return cmd_init_db(args)
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

    parser.print_help(sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
