"""Command-line interface for AutoPromptRunner.

CLI-first entry point (see PROJECT.md). Commands in this step:

* ``version``   -- print the package version.
* ``init-db``   -- create the local SQLite database.
* ``run``       -- execute a single prompt against a provider (only ``mock`` for now),
  persist the run and its step, update the run status, and print a compact report.
* ``list-runs`` -- list recent runs from the database.
* ``show-run``  -- show one run and its steps.

Real provider execution (Claude Code, Codex), next-prompt generation, the approval
gate, and the multi-step loop are not implemented yet.
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, Optional, Sequence, Type

from . import __version__, storage
from .models import RunReport, RunRequest
from .runners import AgentRunner, MockRunner
from .state import RunStatus

# Registry of available providers. Only the mock provider is supported in this step;
# real providers (claude_code, codex) will be registered here as they are implemented.
PROVIDERS: Dict[str, Type[AgentRunner]] = {
    "mock": MockRunner,
}

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
        "run", help="Execute a prompt, persist the run, and print a report."
    )
    run_parser.add_argument("--prompt", required=True, help="Prompt to send. Must not be empty.")
    run_parser.add_argument(
        "--provider", default="mock", help="Provider to use. Only 'mock' is supported in this step."
    )
    run_parser.add_argument(
        "--max-loops", dest="max_loops", type=int, default=1, help="Max agent invocations. Must be >= 1."
    )
    run_parser.add_argument(
        "--auto-run", dest="auto_run", action="store_true",
        help="Skip the approval gate. Reserved for future loops; no effect yet.",
    )
    _add_db_path(run_parser)

    list_parser = subparsers.add_parser("list-runs", help="List recent runs.")
    list_parser.add_argument("--limit", type=int, default=20, help="Maximum number of runs to show.")
    _add_db_path(list_parser)

    show_parser = subparsers.add_parser("show-run", help="Show one run and its steps.")
    show_parser.add_argument("--id", dest="run_id", type=int, required=True, help="Run id to show.")
    _add_db_path(show_parser)

    return parser


def cmd_version() -> int:
    """Print the package version."""
    print(__version__)
    return EXIT_OK


def cmd_init_db(args: argparse.Namespace) -> int:
    """Create the database (and parent directory) and print its path."""
    path = storage.init_db(args.db_path)
    print(f"Database initialized at: {path}")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    """Validate inputs, persist a run + step, run the provider, and print a report."""
    prompt = (args.prompt or "").strip()
    if not prompt:
        print("error: --prompt must not be empty", file=sys.stderr)
        return EXIT_USAGE
    if args.max_loops < 1:
        print("error: --max-loops must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    if args.provider not in PROVIDERS:
        supported = ", ".join(sorted(PROVIDERS))
        print(
            f"error: unsupported provider '{args.provider}'. Supported: {supported}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    request = RunRequest(
        prompt=prompt,
        provider=args.provider,
        max_loops=args.max_loops,
        require_approval=not args.auto_run,
    )

    db_path = storage.init_db(args.db_path)  # ensure DB exists; returns resolved path
    run_id = storage.create_run(
        db_path,
        root_prompt=request.prompt,
        provider=request.provider,
        max_loops=request.max_loops,
        require_approval=request.require_approval,
    )
    storage.update_run_status(db_path, run_id, RunStatus.RUNNING.value)

    runner = PROVIDERS[request.provider]()
    result = runner.run(request.prompt)
    final_status = RunStatus.DONE.value if result.exit_code == 0 else RunStatus.FAILED.value

    storage.create_step(
        db_path,
        run_id=run_id,
        loop_index=0,
        prompt=request.prompt,
        status=final_status,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        started_at=result.started_at,
        finished_at=result.finished_at,
        next_prompt=None,
    )
    storage.update_run_status(db_path, run_id, final_status, finished_at=result.finished_at)

    report = RunReport(
        status=final_status,
        provider=runner.name,
        prompt=request.prompt,
        result=result,
        next_prompt=None,
    )
    _print_report(report, run_id=run_id)
    return EXIT_OK if result.exit_code == 0 else EXIT_RUN_FAILED


def cmd_list_runs(args: argparse.Namespace) -> int:
    """Print a compact list of recent runs."""
    db_path = storage.init_db(args.db_path)
    runs = storage.list_runs(db_path, limit=args.limit)
    if not runs:
        print("No runs found.")
        return EXIT_OK
    print(f"{'ID':>4}  {'STATUS':<8}  {'PROVIDER':<10}  {'CREATED_AT':<32}  PROMPT")
    for run in runs:
        print(
            f"{run.id:>4}  {run.status:<8}  {run.provider:<10}  "
            f"{(run.created_at or ''):<32}  {_shorten(run.root_prompt, 48)}"
        )
    return EXIT_OK


def cmd_show_run(args: argparse.Namespace) -> int:
    """Print one run's detail and its steps, or a clean error if it is missing."""
    db_path = storage.init_db(args.db_path)
    run = storage.get_run(db_path, args.run_id)
    if run is None:
        print(f"error: run {args.run_id} not found", file=sys.stderr)
        return EXIT_NOT_FOUND

    steps = storage.get_steps_for_run(db_path, run.id)
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
        if step.stderr:
            print(f"      stderr     : {_shorten(step.stderr, 80)}")
        print(f"      next_prompt: {step.next_prompt or '(none)'}")
    return EXIT_OK


def _shorten(text: Optional[str], limit: int) -> str:
    """Collapse whitespace and truncate ``text`` to ``limit`` characters."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)] + "..."


def _print_report(report: RunReport, run_id: Optional[int] = None) -> None:
    """Print a compact, human-readable execution report."""
    indent = "\n" + " " * 16
    lines = ["Run report"]
    if run_id is not None:
        lines.append(f"  run_id      : {run_id}")
    lines.extend(
        [
            f"  status      : {report.status}",
            f"  provider    : {report.provider}",
            f"  prompt      : {report.prompt}",
        ]
    )
    result = report.result
    if result is not None:
        lines.append(f"  exit_code   : {result.exit_code}")
        lines.append(f"  started_at  : {result.started_at}")
        lines.append(f"  finished_at : {result.finished_at}")
        stdout = result.stdout.rstrip("\n")
        if stdout:
            lines.append(f"  stdout      : {stdout.replace(chr(10), indent)}")
        stderr = result.stderr.rstrip("\n")
        if stderr:
            lines.append(f"  stderr      : {stderr.replace(chr(10), indent)}")
    lines.append(f"  next_prompt : {report.next_prompt or '(none)'}")
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
    if args.command == "list-runs":
        return cmd_list_runs(args)
    if args.command == "show-run":
        return cmd_show_run(args)

    parser.print_help(sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
