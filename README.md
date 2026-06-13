# AutoPromptRunner

AutoPromptRunner is a local-first prompt orchestration tool. It sends a prompt to a
coding agent, captures the agent's output, generates the next prompt, and decides
whether to continue. All state, logs, and configuration stay on the local machine; no
remote service is required to run it.

The CLI runs a bounded prompt loop against a deterministic `MockRunner`, persists run
history to a local SQLite database, and gates each generated next prompt behind an
approval by default. No external AI tools are called yet, no network access is used,
and there are no third-party runtime dependencies (standard library only).

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

## Prompt Loop and Approval Gate

A run executes one step at a time through a provider runner. After each successful
step the `PromptGenerator` produces a compact, deterministic next prompt (a "continue"
prompt on success, a "fix" prompt on failure) from the previous result.

By default `require_approval` is true: the run executes one step, stores a PENDING
approval with the generated next prompt, and pauses at `WAITING_APPROVAL`. Nothing
else runs until you approve or reject it. This is the safety gate from AGENTS.md.

`--no-approval` disables the gate and auto-runs steps until `--max-loops` is reached or
a step fails. `max_loops` is a hard bound: the loop can never run more than that many
steps, so it can never run forever.

Run status follows `CREATED -> RUNNING -> WAITING_APPROVAL -> DONE / FAILED / STOPPED`:

- `max_loops` reached -> the run is marked `DONE`.
- a step exits non-zero -> the step and a generated fix prompt are stored, and the run
  is marked `FAILED`.
- a pending approval is rejected -> the run is marked `STOPPED`.

## Persistence

Run history is stored in a local SQLite database (standard-library `sqlite3`). By
default it lives at `.autoprompt/autoprompt.db` (relative to the current working
directory); the parent directory is created automatically. Pass `--db-path <path>` to
any database command to use a different location.

Tables: `projects`, `runs`, `steps`, and `approvals` (id, run_id, step_id,
next_prompt, status, created_at, decided_at). Approval status is one of `PENDING`,
`APPROVED`, `REJECTED`.

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

Initialize the database (creates `.autoprompt/autoprompt.db`):

```
python -m autoprompt_runner.cli init-db
```

Start a run with the approval gate (default). This executes the first step, generates
the next prompt, stores a pending approval, and stops at `WAITING_APPROVAL`:

```
python -m autoprompt_runner.cli run --prompt "Improve README" --provider mock --max-loops 3
```

Approve the pending next prompt and execute it as the next step (this may itself stop
at another approval, or finish the run when `max_loops` is reached):

```
python -m autoprompt_runner.cli approve-next --run-id 1
```

Reject the pending next prompt and stop the run:

```
python -m autoprompt_runner.cli reject-next --run-id 1
```

Auto-run without the approval gate, up to `--max-loops` (or until a failure):

```
python -m autoprompt_runner.cli run --prompt "Improve README" --provider mock --max-loops 3 --no-approval
```

List recent runs, and show one run with its steps and any pending approval:

```
python -m autoprompt_runner.cli list-runs
python -m autoprompt_runner.cli show-run --id 1
```

`approve-next` and `reject-next` exit non-zero with a clean error when there is no
pending approval, and `approve-next` also exits non-zero for a run that is already
`DONE`, `FAILED`, or `STOPPED`.

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
