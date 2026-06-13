# AutoPromptRunner

AutoPromptRunner is a local-first prompt orchestration tool. It sends a prompt to a
coding agent, captures the agent's output, generates the next prompt, and decides
whether to continue. All state, logs, and configuration stay on the local machine; no
remote service is required to run it.

The CLI runs a bounded prompt loop, persists run history to a local SQLite database,
and gates each generated next prompt behind an approval by default. Two providers are
available: `mock` (offline, deterministic) and `claude-code` (the real Claude Code
CLI). There are no third-party runtime dependencies (standard library only).

## MVP Workflow

```
User command
  -> create run
  -> execute agent prompt
  -> collect stdout / stderr / result
  -> generate next prompt
  -> wait for approval or auto-run
  -> repeat until done, failed, stopped, or max-loops reached
```

## Requirements

- Python >= 3.11
- No third-party runtime dependencies (standard library only)
- For the `claude-code` provider: the Claude Code CLI installed and authenticated

## Prompt Loop and Approval Gate

A run executes one step at a time through a provider runner. After each successful
step the `PromptGenerator` produces a compact, deterministic next prompt (a "continue"
prompt on success, a "fix" prompt on failure).

By default `require_approval` is true: the run executes one step, stores a PENDING
approval with the generated next prompt, and pauses at `WAITING_APPROVAL` until you
approve or reject it. `--no-approval` disables the gate and auto-runs steps until
`--max-loops` is reached or a step fails. `max_loops` is a hard bound, so the loop can
never run forever.

Run status follows `CREATED -> RUNNING -> WAITING_APPROVAL -> DONE / FAILED / STOPPED`:
`max_loops` reached -> `DONE`; a step exits non-zero -> `FAILED`; a rejected approval
-> `STOPPED`.

## Persistence

Run history is stored in a local SQLite database (standard-library `sqlite3`). By
default it lives at `.autoprompt/autoprompt.db`; the parent directory is created
automatically. Pass `--db-path <path>` to any command to use a different location.
Tables: `projects`, `runs` (including a nullable `workspace` and `timeout_seconds`),
`steps`, and `approvals`.

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

Initialize the database, then start a run with the mock provider (approval gate on by
default), approve or reject the pending next prompt, or auto-run without the gate:

```
python -m autoprompt_runner.cli init-db
python -m autoprompt_runner.cli run --prompt "Improve README" --provider mock --max-loops 3
python -m autoprompt_runner.cli approve-next --run-id 1
python -m autoprompt_runner.cli reject-next --run-id 1
python -m autoprompt_runner.cli run --prompt "Improve README" --provider mock --max-loops 3 --no-approval
```

List recent runs, and show one run with its steps and any pending approval:

```
python -m autoprompt_runner.cli list-runs
python -m autoprompt_runner.cli show-run --id 1
```

## Claude Code provider

The `claude-code` provider runs the real Claude Code CLI as a subprocess.

- **Requirement:** the Claude Code CLI must already be installed and authenticated on
  this machine. AutoPromptRunner does not install it and never handles API keys.
- **Example:**

  ```
  python -m autoprompt_runner.cli run \
    --prompt "Review this project and suggest the next smallest implementation task" \
    --provider claude-code \
    --workspace /path/to/project \
    --max-loops 1
  ```

- **Workspace:** `--workspace` is required for `claude-code` and must be an existing
  directory; the CLI runs Claude Code with that directory as its working directory.
  The CLI rejects a missing or non-existent workspace with a clean error.
- **Timeout:** `--timeout-seconds` (default 1800, must be >= 1) bounds the subprocess.
  On timeout the step is recorded as a non-zero result with a clear message rather than
  hanging. A missing `claude` command is likewise captured as a clean failed result.
- **Approval gate:** the approval behavior is identical to the mock provider. By
  default the run stops at `WAITING_APPROVAL` after the first step; use `approve-next`
  to run the next step or `reject-next` to stop. `--no-approval` auto-runs up to
  `--max-loops`.
- **Safety warning:** Claude Code may create, modify, or delete files inside the
  workspace. Point `--workspace` only at a project you intend Claude Code to change,
  ideally one tracked in version control.

## Runner Providers

| Provider | Class | Status | Description |
| --- | --- | --- | --- |
| `mock` | `MockRunner` | Available | Deterministic, offline runner used for tests and dry runs. Default provider. |
| `claude-code` | `ClaudeCodeRunner` | Available | Runs the Claude Code CLI as a subprocess inside a workspace. |
| `codex` | `CodexRunner` | Planned | Invocation of the Codex coding agent CLI. |

## Tests

The tests use the Python standard library only and can be run with `unittest`, no
installation required (the Claude Code subprocess is faked, so no real `claude` is
needed):

```
python -m unittest discover -s tests -v
```

## Project Documents

- [PROJECT.md](PROJECT.md) - product specification, architecture, and state machine.
- [AGENTS.md](AGENTS.md) - operating rules for coding agents working in this repository.
