# AutoPromptRunner

AutoPromptRunner is a local-first prompt orchestration tool. It sends a prompt to a
coding agent, captures the agent's output, generates the next prompt, and decides
whether to continue. All state, logs, and configuration stay on the local machine; no
remote service is required to run it.

The CLI runs a bounded prompt loop, persists run history to a local SQLite database,
gates each generated next prompt behind an approval by default, and supports reusable
project profiles so you do not repeat workspace/provider/limit flags on every run. Two
providers are available: `mock` (offline, deterministic) and `claude-code` (the real
Claude Code CLI). There are no third-party runtime dependencies (standard library only).

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

A run executes one step at a time. After each successful step the `PromptGenerator`
produces a deterministic next prompt. By default the run pauses at `WAITING_APPROVAL`
with a PENDING approval; `--no-approval` auto-runs up to `--max-loops`. `max_loops` is
a hard bound, so the loop can never run forever. Status follows
`CREATED -> RUNNING -> WAITING_APPROVAL -> DONE / FAILED / STOPPED`.

## Persistence

Run history is stored in a local SQLite database (standard-library `sqlite3`). By
default it lives at `.autoprompt/autoprompt.db`; the parent directory is created
automatically. Pass `--db-path <path>` to any command to use a different location.
Tables: `projects`, `settings` (default project), `runs`, `steps`, and `approvals`.

## Installation (optional)

The CLI runs directly from the source tree without installing anything. To expose the
`autoprompt-runner` command instead, install the package in editable mode:

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

Initialize the database, run with the mock provider (approval gate on by default),
approve/reject the pending next prompt, or auto-run without the gate:

```
python -m autoprompt_runner.cli init-db
python -m autoprompt_runner.cli run --prompt "Improve README" --provider mock --max-loops 3
python -m autoprompt_runner.cli approve-next --run-id 1
python -m autoprompt_runner.cli reject-next --run-id 1
python -m autoprompt_runner.cli run --prompt "Improve README" --provider mock --max-loops 3 --no-approval
python -m autoprompt_runner.cli list-runs
python -m autoprompt_runner.cli show-run --id 1
```

## Project profiles

A project profile stores reusable run settings (repo path, provider, max loops,
approval, timeout) so you do not pass them on every run.

```
python -m autoprompt_runner.cli project add \
  --name FactoryColony \
  --repo-path /path/to/FactoryColony \
  --provider claude-code \
  --max-loops 5 \
  --timeout-seconds 1800

python -m autoprompt_runner.cli project list
python -m autoprompt_runner.cli project show --name FactoryColony
python -m autoprompt_runner.cli project set-default --name FactoryColony
python -m autoprompt_runner.cli project delete --name FactoryColony
```

`project add` validates that `--repo-path` exists and is a directory, the provider is
supported, and the limits are >= 1. `project list` marks the default project with `*`.

Run using a project's settings, or the default project when `--project` is omitted:

```
# Use the named project's settings (workspace comes from its repo_path)
python -m autoprompt_runner.cli run --project FactoryColony --prompt "Continue next task"

# Use the default project's settings
python -m autoprompt_runner.cli run --prompt "Continue next task"
```

### Override precedence

Settings are resolved in this order (highest wins):

1. Explicit CLI arguments (e.g. `--provider`, `--max-loops`, `--timeout-seconds`, `--workspace`, `--no-approval`)
2. The selected project profile (`--project NAME`)
3. The default project profile
4. Built-in defaults (`mock`, max-loops 1, timeout 1800, approval on)

For the `claude-code` provider the workspace comes from the project's `repo_path`
unless `--workspace` is passed.

> Deleting a project profile removes only the stored settings. It does **not** delete
> the repository or any files on disk. If the deleted project was the default, the
> default is cleared.

## Claude Code provider

The `claude-code` provider runs the real Claude Code CLI as a subprocess.

- **Requirement:** the Claude Code CLI must already be installed and authenticated.
  AutoPromptRunner does not install it and never handles API keys.
- **Example:**

  ```
  python -m autoprompt_runner.cli run \
    --prompt "Review this project and suggest the next smallest implementation task" \
    --provider claude-code \
    --workspace /path/to/project \
    --max-loops 1
  ```

- **Workspace:** `--workspace` is required for `claude-code` and must be an existing
  directory (or supplied via a project's `repo_path`). The CLI runs Claude Code with
  that directory as its working directory.
- **Timeout:** `--timeout-seconds` (default 1800, must be >= 1) bounds the subprocess.
  Timeout and a missing `claude` command are captured as clean non-zero results rather
  than hanging or crashing.
- **Approval gate:** identical to the mock provider; the run stops at
  `WAITING_APPROVAL` after the first step unless `--no-approval` is given.
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

Standard library only; the Claude Code subprocess is faked, so no real `claude` is
needed:

```
python -m unittest discover -s tests -v
```

## Project Documents

- [PROJECT.md](PROJECT.md) - product specification, architecture, and state machine.
- [AGENTS.md](AGENTS.md) - operating rules for coding agents working in this repository.
