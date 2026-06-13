# AutoPromptRunner

AutoPromptRunner is a local-first prompt orchestration tool. It sends a prompt to a
coding agent, captures the agent's output, and decides whether to continue based on
the result. All state, logs, and configuration stay on the local machine; no remote
service is required to run it.

This repository is at an early stage. The CLI runs end to end against a deterministic
`MockRunner` and now persists run history to a local SQLite database. No external AI
tools are called yet, and there are no third-party runtime dependencies (standard
library only).

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

The current implementation covers the first legs of that workflow: a single prompt is
executed by the `MockRunner`, the run and its step are persisted, the run status is
advanced (`CREATED -> RUNNING -> DONE/FAILED`), and a compact report is printed.
Next-prompt generation, the approval gate, and the multi-step loop are planned. See
[PROJECT.md](PROJECT.md) for the full specification.

## Requirements

- Python >= 3.11
- No third-party runtime dependencies (standard library only)

## Persistence

Run history is stored in a local SQLite database using the standard-library `sqlite3`
module. By default the database lives at `.autoprompt/autoprompt.db` (relative to the
current working directory); the parent directory is created automatically. Pass
`--db-path <path>` to any database command to use a different location.

Three tables are used:

- `projects` — `id`, `name`, `repo_path`, `created_at`.
- `runs` — `id`, `project_id`, `root_prompt`, `provider`, `status`, `max_loops`,
  `require_approval`, `created_at`, `finished_at`.
- `steps` — `id`, `run_id`, `loop_index`, `prompt`, `stdout`, `stderr`, `exit_code`,
  `status`, `started_at`, `finished_at`, `next_prompt`.

Run status follows the practical subset `CREATED -> RUNNING -> DONE / FAILED / STOPPED`;
illegal transitions are rejected before they reach the database.

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

Initialize the database (creates `.autoprompt/autoprompt.db`):

```
python -m autoprompt_runner.cli init-db
```

Run a prompt against the mock provider (creates and persists a run):

```
python -m autoprompt_runner.cli run --prompt "Improve README" --provider mock --max-loops 1
```

List recent runs (id, status, provider, created_at, short prompt):

```
python -m autoprompt_runner.cli list-runs
```

Show one run and its steps:

```
python -m autoprompt_runner.cli show-run --id 1
```

The `run` command validates that the prompt is non-empty and that `--max-loops` is at
least 1, ensures the database exists, persists the run and its step, updates the run
status, and prints a compact execution report including the run id. `show-run` exits
with a non-zero status if the given id does not exist.

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
