"""Command-line interface for AutoPromptRunner.

This is the CLI-first entry point described in PROJECT.md. In this step it supports
two commands:

* ``version`` -- print the package version.
* ``run``     -- execute a single prompt against a provider (only ``mock`` for now)
  and print a compact execution report.

Real provider execution (Claude Code, Codex), result summarization, next-prompt
generation, the approval gate, the loop, and persistence are not implemented yet.
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, Optional, Sequence, Type

from . import __version__
from .models import RunReport, RunRequest
from .runners import AgentRunner, MockRunner

# Registry of available providers. Only the mock provider is supported in this step;
# real providers (claude_code, codex) will be registered here as they are implemented.
PROVIDERS: Dict[str, Type[AgentRunner]] = {
    "mock": MockRunner,
}

# Exit codes.
EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the ``version`` and ``run`` commands."""
    parser = argparse.ArgumentParser(
        prog="autoprompt-runner",
        description="Local-first prompt orchestration tool (CLI skeleton).",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    subparsers.add_parser("version", help="Print the package version.")

    run_parser = subparsers.add_parser(
        "run",
        help="Execute a single prompt against a provider and print a report.",
    )
    run_parser.add_argument(
        "--prompt",
        required=True,
        help="The prompt to send to the provider. Must not be empty.",
    )
    run_parser.add_argument(
        "--provider",
        default="mock",
        help="Runner provider to use. Only 'mock' is supported in this step.",
    )
    run_parser.add_argument(
        "--max-loops",
        dest="max_loops",
        type=int,
        default=1,
        help="Maximum number of agent invocations. Must be >= 1.",
    )
    run_parser.add_argument(
        "--auto-run",
        dest="auto_run",
        action="store_true",
        help="Skip the approval gate. Reserved for future loops; no effect yet.",
    )
    return parser


def cmd_version() -> int:
    """Print the package version."""
    print(__version__)
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    """Validate inputs, run the selected provider, and print a compact report."""
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

    runner = PROVIDERS[request.provider]()
    result = runner.run(request.prompt)

    status = "DONE" if result.exit_code == 0 else "FAILED"
    report = RunReport(
        status=status,
        provider=runner.name,
        prompt=request.prompt,
        result=result,
        next_prompt=None,
    )
    _print_report(report)
    return EXIT_OK if result.exit_code == 0 else EXIT_RUN_FAILED


def _print_report(report: RunReport) -> None:
    """Print a compact, human-readable execution report."""
    indent = "\n" + " " * 16
    lines = [
        "Run report",
        f"  status      : {report.status}",
        f"  provider    : {report.provider}",
        f"  prompt      : {report.prompt}",
    ]
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
    if args.command == "run":
        return cmd_run(args)

    parser.print_help(sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
