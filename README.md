# AutoPromptRunner

AutoPromptRunner is a local-first prompt orchestration tool. It sends a prompt to a
coding agent, captures the agent's output, and decides whether to continue based on
the result. All state, logs, and configuration stay on the local machine; no remote
service is required to run it.

This repository is at an early skeleton stage. The CLI runs end to end against a
deterministic `MockRunner`. No external AI tools are called yet, and there are no
third-party runtime dependencies (standard library only).

## MVP Workflow

```
User command
  -> create run
  -> execute agent prompt
  -> collect stdout / stderr / result
  -> summarize result
  -> generate next prompt
  -> wait for approval or auto-run
  -> repeat until done, failed, stopped, or max-loops reached
```

This skeleton implements the first leg of that workflow: a single prompt is executed
by the `MockRunner` and a compact execution report is printed. Result summarization,
next-prompt generation, the approval gate, the loop, and persistence are planned. See
[PROJECT.md](PROJECT.md) for the full specification.

## Requirements

- Python >= 3.11
- No third-party runtime dependencies (standard library only)

## Installation (optional)

The CLI can be run directly from the source tree without installing anything. To
expose the `autoprompt-runner` command instead, install the package in editable mode:

```
pip install -e .
```

## CLI Usage

When running from the source tree (without installing), put `src` on the import path:

```
# Windows PowerShell
$env:PYTHONPATH = "src"; python -m autoprompt_runner.cli version

# Linux / macOS
PYTHONPATH=src python -m autoprompt_runner.cli version
```

Print the package version:

```
python -m autoprompt_runner.cli version
```

Run a single prompt against the mock provider:

```
python -m autoprompt_runner.cli run --prompt "Improve README" --provider mock --max-loops 1
```

The `run` command validates that the prompt is non-empty and that `--max-loops` is at
least 1, executes the selected provider, and prints a compact execution report.

## Runner Providers

| Provider | Class | Status | Description |
| --- | --- | --- | --- |
| `mock` | `MockRunner` | Available | Deterministic, offline runner used for tests and dry runs. The only provider supported in this step. |
| `claude_code` | `ClaudeCodeRunner` | Planned | Subprocess invocation of the Claude Code CLI. |
| `codex` | `CodexRunner` | Planned | Invocation of the Codex coding agent CLI. |

Only `MockRunner` is implemented today. `ClaudeCodeRunner` and `CodexRunner` are
future providers and are not available yet.

## Tests

The tests use the Python standard library only and can be run with `unittest`, no
installation required:

```
python -m unittest discover -s tests -v
```

When `pytest` is installed, the same tests are collected and run under `pytest`.

## Project Documents

- [PROJECT.md](PROJECT.md) - product specification, architecture, and state machine.
- [AGENTS.md](AGENTS.md) - operating rules for coding agents working in this repository.
