# AutoPromptRunner

AutoPromptRunner is a local-first prompt orchestration tool. It sends a prompt to a
coding agent, captures the agent's output, generates the next prompt, and decides
whether to continue. All state, logs, and configuration stay on the local machine; no
remote service is required to run it.

The CLI runs a bounded prompt loop, persists run history to a local SQLite database,
gates each generated next prompt behind an approval by default, supports reusable
project profiles, and captures the Git state around each step (read-only) so every run
records what changed. Three providers are available: `mock` (offline, deterministic),
`claude-code` (the real Claude Code CLI), and `codex` (the real Codex CLI). The CLI core
has no third-party runtime dependencies (standard library only); an optional FastAPI
HTTP backend is available via the `api` extra.

This is the **v0.1 MVP**: a stable, local-first end-to-end tool. See the section index
above (or scroll) for the full feature tour; each capability has its own section below.

## v0.1 capabilities

- Local-first execution (no remote service required)
- CLI, optional FastAPI backend, and a React/Vite frontend
- SQLite persistence of projects, runs, steps, approvals, and artifacts
- Providers: `mock` (offline), `claude-code`, and `codex`
- Project profiles and reusable prompt templates
- Bounded prompt loop with an approval gate
- Read-only Git artifact capture (status / diff / changed files)
- Git worktree parallel sessions and workspace execution locks
- Local run queue with a background worker, and run cancellation
- Search across runs, logs, prompts, and artifacts (SQLite `LIKE`)
- Compare two runs (metadata, steps, changed files, diff stats, artifacts)
- Prompt chain history timeline (root → step prompts → next prompts, per run)
- Provider profiles (configurable command / timeout / args + availability checks)
- Failure recovery (rule-based recovery prompt + new linked recovery run)
- Portable JSON export / import (best-effort redaction, non-destructive import)
- Config file + environment overrides
- Safety checks (blocked commands, secret-file warnings, hard limits)

## Not supported yet (post-v0.1)

- Authentication and multi-user deployment
- Distributed workers or a hosted/cloud service
- WebSocket / SSE live streaming (run logs use polling)
- Cloud sync and browser automation
- Guaranteed process cancellation across machine restarts
- Full provider-specific advanced options

## Install and setup

**One-command local setup** (creates a `.venv`, installs the backend in editable mode,
installs the frontend dependencies, writes `.autoprompt/config.toml`, and seeds the built-in
templates and provider profiles -- it never overwrites an existing config unless you pass
`--force`, and never requires Claude Code or Codex):

```
git clone https://github.com/opop20207/AutoPromptRunner.git
cd AutoPromptRunner
scripts/setup_local.sh            # or: scripts/setup_local.sh --force (overwrite config)
```

**Manual install** -- backend and frontend separately:

```
pip install -e ".[dev]"           # CLI + FastAPI backend + test client (scripts/install_backend.sh)
( cd frontend && npm install )    # web UI dependencies              (scripts/install_frontend.sh)
```

The CLI core needs no third-party packages; the `[dev]` (or `[api]`) extra adds FastAPI for
the HTTP backend. The console entry point `autoprompt-runner` is installed by pip (equivalent
to `python -m autoprompt_runner.cli`). A first run:

```
python -m autoprompt_runner.cli init-db                 # create the local SQLite database
python -m autoprompt_runner.cli config init             # optional: write .autoprompt/config.toml
python -m autoprompt_runner.cli template seed           # add the built-in prompt templates
python -m autoprompt_runner.cli provider seed           # add the default provider profiles
python -m autoprompt_runner.cli project add --name demo --repo-path . --provider mock
python -m autoprompt_runner.cli run --project demo --prompt "Review this project" --max-loops 3
python -m autoprompt_runner.cli approve-next --run-id 1 # or: reject-next --run-id 1
```

### Scripts

All helper scripts are bash, use `set -euo pipefail` (where practical), resolve the project
root safely, run no destructive commands, and never invoke Claude Code, Codex, or any
external AI tool. They live in [`scripts/`](scripts):

| Script | What it does |
| --- | --- |
| `setup_local.sh` | One-command setup (venv + backend + frontend + config + seed). `--force` overwrites config. |
| `install_backend.sh` | `pip install -e ".[dev]"`, then print the CLI version. |
| `install_frontend.sh` | `npm install` inside `frontend/` (no global packages). |
| `build_frontend.sh` | Build the frontend (`tsc` + `vite build`); fails cleanly on TS/build errors. |
| `dev_api.sh` | Start the FastAPI dev server (`AUTOPROMPT_API_HOST`/`AUTOPROMPT_API_PORT`, default `127.0.0.1:8000`). |
| `dev_worker.sh` | Start the local queue worker (forwards flags like `--once`, `--poll-interval-seconds`). |
| `dev_frontend.sh` | Start the Vite dev server (`http://localhost:5173`). |
| `check_all.sh` | Backend tests + `config validate` + mock provider check + frontend build (safe; no external AI). |
| `doctor.sh` | Environment diagnostics (Python, Node/npm, SQLite, CLI, config, frontend deps, optional `claude`/`codex`). |
| `package_release.sh` | Run checks, build the frontend and (if `build` is installed) the wheel/sdist, and assemble `dist/release-v0.1`. It does **not** publish, tag, or create a GitHub release. |

```
scripts/check_all.sh        # verify the whole project before committing
scripts/doctor.sh           # diagnose a local environment (exits non-zero only on a required failure)
scripts/package_release.sh  # assemble a local v0.1 release under dist/release-v0.1
```

## MVP Workflow

```
User command
  -> create run
  -> capture git status (before)
  -> execute agent prompt
  -> capture git status / diff / changed files (after)
  -> generate next prompt
  -> wait for approval or auto-run
  -> repeat until done, failed, stopped, or max-loops reached
```

## Requirements

- Python >= 3.11 (uses the standard-library `tomllib` for config files)
- No third-party runtime dependencies (standard library only)
- For the `claude-code` provider: the Claude Code CLI installed and authenticated
- For Git artifact capture: the `git` CLI (optional; skipped when absent or non-repo)

## Configuration

CLI, API, and worker share one settings loader so they behave consistently. Settings come
from a local **TOML config file** plus **`AUTOPROMPT_*` environment variables**, layered
over built-in defaults -- **no config file is required**.

**Search order** (first match wins): explicit `--config <path>` → `AUTOPROMPT_CONFIG` env
→ `./autoprompt.toml` → `./.autoprompt/config.toml` → built-in defaults.

**Override precedence**, lowest to highest: built-in defaults → config file → environment
variables → (for a run) project profile → explicit CLI flags. In other words, a CLI flag
or project profile always wins at execution time; config/env set the defaults beneath them.

A complete example is in [`autoprompt.example.toml`](autoprompt.example.toml); the sections
and built-in defaults are:

```toml
[storage]
db_path = ".autoprompt/autoprompt.db"

[defaults]
provider = "mock"
workspace = ""
max_loops = 5
require_approval = true
timeout_seconds = 1800

[safety]
max_loops_hard_limit = 20
timeout_seconds_hard_limit = 7200
large_changed_files_threshold = 20
large_diff_lines_threshold = 1000

[queue]
poll_interval_seconds = 2

[api]
host = "127.0.0.1"
port = 8000

[worktrees]
base_dir = ".autoprompt/worktrees"
```

**Environment overrides** (see [`.env.example`](.env.example)): `AUTOPROMPT_CONFIG`,
`AUTOPROMPT_DB_PATH`, `AUTOPROMPT_DEFAULT_PROVIDER`, `AUTOPROMPT_DEFAULT_WORKSPACE`,
`AUTOPROMPT_MAX_LOOPS_DEFAULT`, `AUTOPROMPT_MAX_LOOPS_HARD_LIMIT`,
`AUTOPROMPT_TIMEOUT_SECONDS_DEFAULT`, `AUTOPROMPT_TIMEOUT_SECONDS_HARD_LIMIT`,
`AUTOPROMPT_QUEUE_POLL_INTERVAL_SECONDS`, `AUTOPROMPT_API_HOST`, `AUTOPROMPT_API_PORT`,
`AUTOPROMPT_WORKTREE_BASE_DIR`.

CLI:

```
python -m autoprompt_runner.cli config init                 # write .autoprompt/config.toml (use --force to overwrite)
python -m autoprompt_runner.cli --config autoprompt.toml config show       # print the effective config
python -m autoprompt_runner.cli config validate             # exit non-zero if the effective config is invalid
```

Pass the global `--config <path>` **before** the command (e.g. `--config x.toml run ...`).
Validation rejects an empty `db_path`, non-positive limits, `max_loops` above the hard
limit, and `timeout_seconds` above its hard limit.

- **Storage** uses `storage.db_path` by default (the built-in `.autoprompt/autoprompt.db`
  still applies when nothing is configured).
- **Worker** uses `queue.poll_interval_seconds` by default; `--poll-interval-seconds`
  overrides it.
- **API** uses the same loader, and `GET /health` returns compact, non-secret config
  metadata (`db_path`, `default_provider`, `queue_poll_interval_seconds`, and the safety
  hard limits) -- never environment dumps or secrets.

> **Never store secrets in the config file or `AUTOPROMPT_*` variables.** These are
> non-secret operational settings only; AutoPromptRunner reads no credentials from them.

## Prompt Loop and Approval Gate

A run executes one step at a time. After each successful step the `PromptGenerator`
produces a deterministic next prompt. By default the run pauses at `WAITING_APPROVAL`
with a PENDING approval; `--no-approval` auto-runs up to `--max-loops`. `max_loops` is
a hard bound, so the loop can never run forever. Status follows
`CREATED -> RUNNING -> WAITING_APPROVAL -> DONE / FAILED / STOPPED`.

## Safety hardening and execution limits

Safety checks are **deterministic, offline, and read-only** (no network, no AI). They
run in `autoprompt_runner.safety` against the constants in `autoprompt_runner.config`.

- **Execution limits (hard bounds).** `--max-loops` defaults to the project/built-in
  value and is rejected above the hard limit of **20**; `--timeout-seconds` defaults to
  1800 and is rejected above the hard limit of **7200**. The CLI exits non-zero and the
  API returns `400` when a value exceeds its hard limit; runner timeouts are also clamped
  to the hard limit.
- **Blocked command patterns (pre-execution).** The prompt is scanned **before** the
  runner executes. If it contains a destructive pattern -- e.g. `rm -rf /`, `rm -rf *`,
  `git reset --hard`, `git clean -fd`, `git push --force`, `sudo rm`, `del /s`, `format`,
  `mkfs`, `shutdown`, `reboot` -- the run is marked `FAILED`, a `safety_blocker` artifact
  is recorded, the runner never runs, and the API returns `400`. Matching is word-boundary
  and case-insensitive, so ordinary words (e.g. "information") are not false positives.
- **Secret-file denylist (post-step, name-only).** After a step, changed file **names**
  are matched against the secret denylist (`.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa`,
  `id_dsa`, `id_ed25519`, `secrets.*`, `credentials.*`, `service-account*.json`, `*.p12`,
  `*.pfx`). **Secret file contents are never read or printed** -- only the path/basename
  is inspected -- and a compact `safety_warning` artifact records the match.
- **Large-diff warning.** A change touching more than **20** files, or more than **1000**
  combined insertions/deletions (parsed from the diff stat), records a `safety_warning`.
- **Risky runs force approval.** When a step produces a secret-file or large-diff finding,
  the run pauses at `WAITING_APPROVAL` **even when `--no-approval` was passed**, so a human
  reviews the change before it continues.
- **Workspace allowlist (optional).** Set `AUTOPROMPT_WORKSPACE_ALLOWLIST` (OS path-list
  separated) to restrict runs to workspaces contained within those roots; an out-of-root
  workspace is rejected with a clean error. Unset means no restriction.

Check a prompt (and optional workspace) without executing anything:

```
python -m autoprompt_runner.cli safety-check --prompt "Continue next task"
python -m autoprompt_runner.cli safety-check --prompt "rm -rf / the repo" --workspace /path/to/project
```

`safety-check` prints blockers and warnings and exits non-zero when any blocker is found.
The `run` command prints a compact list of safety warnings after the run. Safety findings
are stored as artifacts (`safety_blocker`, `safety_warning`) and surfaced in the API run
response (`warnings`) and the Web UI **Safety** panel.

## Next-prompt generation

The next prompt is produced by a **rule-based** generator -- it calls no external AI
APIs and uses no network. It chooses an outcome-specific prompt from the step context
(root prompt, previous prompt, stdout, stderr, exit code, loop index, max loops, the
changed files, and the git diff stat):

- **Success with changed files** -> review the changed files, run/improve tests, do the
  next smallest task, do not expand scope.
- **Success with no changed files** -> check whether the task is already complete and
  make only the next smallest concrete change if needed.
- **Failure with stderr** -> fix using stderr as the primary source, do not expand
  scope, re-run, and report remaining blockers.
- **Failure without stderr** -> diagnose from stdout and workspace state and make a
  minimal fix.
- **Test-failure output** (traceback, AssertionError, "failed", pytest/unittest, ...)
  -> fix the failing tests first while preserving intended behavior.
- **Many changed files** -> review the broad changes, reduce scope, and check for
  accidental modifications.
- **Final loop** (`loop_index + 1 >= max_loops`) -> wrap up: summarize work and list
  remaining tasks; do not start large new work.

Generated prompts are compact and actionable and contain no invented file paths or test
results. Add `--show-next-prompt` to `run` or `approve-next` to print the full generated
prompt instead of only the compact preview:

```
python -m autoprompt_runner.cli run --prompt "Continue next task" --max-loops 3 --show-next-prompt
python -m autoprompt_runner.cli approve-next --run-id 1 --show-next-prompt
```

## Persistence

Run history is stored in a local SQLite database (standard-library `sqlite3`). By
default it lives at `.autoprompt/autoprompt.db`; the parent directory is created
automatically. Pass `--db-path <path>` to any command to use a different location.
Tables: `projects`, `settings`, `runs`, `steps`, `approvals`, and `artifacts`.

## Project profiles

A project profile stores reusable run settings (repo path, provider, max loops,
approval, timeout) so you do not pass them on every run.

```
python -m autoprompt_runner.cli project add \
  --name FactoryColony --repo-path /path/to/FactoryColony \
  --provider claude-code --max-loops 5 --timeout-seconds 1800
python -m autoprompt_runner.cli project list
python -m autoprompt_runner.cli project show --name FactoryColony
python -m autoprompt_runner.cli project set-default --name FactoryColony
python -m autoprompt_runner.cli project delete --name FactoryColony
```

Run using a project's settings, or the default project when `--project` is omitted:

```
python -m autoprompt_runner.cli run --project FactoryColony --prompt "Continue next task"
python -m autoprompt_runner.cli run --prompt "Continue next task"
```

Settings resolve with precedence: explicit CLI args > selected `--project` > default
project > built-in defaults (`mock`, max-loops 1, timeout 1800, approval on). For
`claude-code`, the workspace comes from the project's `repo_path` unless `--workspace`
is passed. Deleting a project profile removes only the stored settings; it does **not**
delete the repository or any files on disk, and clears the default if it was default.

## Prompt templates

A **prompt template** stores reusable prompt text so common agent workflows can be
started quickly. A template has a name, description, tags, and a plain-text body that may
contain `{{placeholder}}` tokens. Templates are persisted in the same local SQLite
database (the `templates` table) and are independent of runs -- deleting a template never
deletes any runs.

**Placeholder rules** (rendering is plain, deterministic string substitution -- no code
is executed and no expression is evaluated):

- Supported placeholders: `{{project_name}}`, `{{workspace}}`, `{{goal}}`,
  `{{changed_files}}`, `{{last_error}}`, `{{extra_context}}`.
- A supported placeholder with a missing value renders as an empty string.
- Any **unknown** placeholder (for example `{{foo}}`) is left exactly as written.

**Built-in templates** (added by `template seed`, never overwriting a template you have
modified unless you pass `--force`): *Continue next task*, *Fix failing tests*, *Review
git diff*, *Refactor small module*, *Update documentation*, *Generate next prompt only*,
*Diagnose failure*, and *Reduce scope after large diff*.

CLI:

```
python -m autoprompt_runner.cli template seed                       # insert built-ins if missing
python -m autoprompt_runner.cli template list
python -m autoprompt_runner.cli template show --name "Fix failing tests"
python -m autoprompt_runner.cli template add \
  --name "Small implementation step" \
  --description "Implement the next smallest task safely" \
  --body "Implement the next smallest task for {{project_name}}. Goal: {{goal}}"
python -m autoprompt_runner.cli template render --name "Fix failing tests" \
  --project FactoryColony --goal "Fix current test failures"
python -m autoprompt_runner.cli template delete --name "Small implementation step"
```

Run from a template instead of a direct prompt (the template body is rendered and used as
the run prompt). Passing both `--prompt` and `--template` is rejected with a clean error;
the existing direct `--prompt` behavior is unchanged:

```
python -m autoprompt_runner.cli run --project FactoryColony \
  --template "Fix failing tests" --goal "Fix failing placement preview tests"
```

API (`/templates` route group; same local database as the CLI):

```
curl -X POST http://127.0.0.1:8000/templates/seed
curl http://127.0.0.1:8000/templates
curl -X POST http://127.0.0.1:8000/templates \
  -H "Content-Type: application/json" \
  -d '{"name":"Small step","body":"Implement the next task for {{project_name}}. Goal: {{goal}}","tags":["impl"]}'
curl http://127.0.0.1:8000/templates/Small%20step
curl -X POST http://127.0.0.1:8000/templates/Fix%20failing%20tests/render \
  -H "Content-Type: application/json" -d '{"goal":"Fix the preview tests"}'
curl -X DELETE http://127.0.0.1:8000/templates/Small%20step
```

`POST /runs` also accepts `template`, `goal`, and `extra_context`: when `template` is
given it is rendered and used as the prompt; supplying both `prompt` and `template`
returns `400`, and project/default resolution is unchanged.

In the **web UI**, the *Templates* section lists templates (name, description, tags) with
a *Seed built-ins* button and per-row *Use* / *Delete*; *New Template* creates a custom
template. *New Run* has a template selector plus *Goal* and *Extra context* fields: pick a
template to run from it (the direct prompt is disabled while a template is selected) and
use *Preview rendered prompt* to see the rendered text before starting the run.

## Parallel sessions with Git worktrees

Parallel agent sessions must **not** run inside the same working tree: two agents editing
one directory at once corrupt each other's changes and the Git state. AutoPromptRunner
gives each parallel session its own **Git worktree** -- an isolated directory on its own
branch, linked to the same repository -- so sessions never collide. A worktree profile
(project, name, branch, base_branch, path, status of `ACTIVE` / `LOCKED` / `ARCHIVED`) is
recorded in the database; the directory itself is managed **only** through
`git worktree` commands.

Worktrees are created under `.autoprompt/worktrees/{project_name}/{worktree_name}`.

```
python -m autoprompt_runner.cli worktree create \
  --project FactoryColony --name ui-session \
  --branch autoprompt/ui-session --base-branch main
python -m autoprompt_runner.cli worktree list --project FactoryColony
python -m autoprompt_runner.cli worktree show --name ui-session
python -m autoprompt_runner.cli worktree archive --name ui-session   # keeps the files on disk
python -m autoprompt_runner.cli worktree remove --name ui-session    # git worktree remove + drop record
```

Run inside a worktree's isolated path with `--worktree`:

```
python -m autoprompt_runner.cli run --project FactoryColony \
  --worktree ui-session --template "Continue next task" \
  --goal "Implement UI shell improvements"
```

**Workspace override precedence** (highest first):

1. explicit `--workspace`,
2. the selected `--worktree` path,
3. the selected project's `repo_path`,
4. the default project's `repo_path`.

A named worktree is always validated: a missing worktree is a clean "not found" error and
an `ARCHIVED` worktree is refused. (For the inverse safety rule -- never run two sessions
in one directory -- create a separate worktree per session rather than pointing multiple
runs at the same path.)

**Safe removal.** Removal uses `git worktree remove` only -- the tool never deletes a
worktree folder manually and never runs `reset` / `clean` / `push` / `pull` / `merge` /
`rebase`. `remove` refuses a worktree that has an active (RUNNING / WAITING_APPROVAL) run
unless `--force` is given (which also passes `--force` to `git worktree remove` for a
dirty worktree); the DB record is dropped only after the git removal succeeds, and run
history is never deleted. `archive` only flips the status to `ARCHIVED` and leaves every
file on disk untouched.

API (`/worktrees` route group; `POST /runs` also accepts a `worktree` field with the same
precedence and `400`/`404` rules):

```
curl -X POST http://127.0.0.1:8000/worktrees \
  -H "Content-Type: application/json" \
  -d '{"project":"FactoryColony","name":"ui-session","branch":"autoprompt/ui-session","base_branch":"main"}'
curl http://127.0.0.1:8000/worktrees
curl http://127.0.0.1:8000/worktrees/ui-session
curl -X POST http://127.0.0.1:8000/worktrees/ui-session/archive
curl -X DELETE http://127.0.0.1:8000/worktrees/ui-session
```

In the **web UI**, *New Worktree* creates one (project, name, branch, base branch); the
*Worktrees* section lists them (project, name, branch, status, path) with *Archive* and
*Remove* (with a confirmation), and *New Run* has a worktree selector that shows the
resolved workspace path.

## Workspace execution locks

AutoPromptRunner may drive Claude Code or Codex against real repositories. Two active
runs in the **same workspace** can corrupt edits, mix diffs, and create invalid run
history. To prevent this, a run takes a **workspace execution lock** before any runner
executes: there is at most **one active lock per workspace** (paths are normalized first,
so differently-written paths to the same directory share one lock).

**Lifecycle.** The lock is held only during actual runner execution and is released as
soon as the run reaches a terminal state (`DONE` / `FAILED` / `STOPPED`) **or** pauses at
`WAITING_APPROVAL` -- so a run waiting for human approval never blocks the workspace.
`approve-next` re-acquires the lock before running the next step; `reject-next` releases
it. A run with no workspace (for example a `mock` run) needs no lock. A run that uses a
project's `repo_path`, a worktree path, or an explicit `--workspace` is locked. If another
active run already holds the workspace, the new run is refused with a clean error (CLI
exits non-zero, API returns `409`) and a `lock_blocker` artifact is recorded.

**Expiration.** Each lock has an `expires_at` of the run's `timeout_seconds + 300`. If a
process dies before releasing its lock, the stale lock is reclaimed automatically the next
time a lock is acquired or listed -- so a crash cannot block a workspace forever. The
`locks release` command (and `POST /locks/{run_id}/release`) is a manual escape hatch.

CLI:

```
python -m autoprompt_runner.cli locks list                 # id, run, status, expires_at, workspace
python -m autoprompt_runner.cli locks release --run-id 12  # manually release a stale lock
```

A `run` whose workspace is locked by another active run prints a compact lock error and
exits non-zero.

API:

```
curl http://127.0.0.1:8000/locks
curl -X POST http://127.0.0.1:8000/locks/12/release
```

`POST /runs` returns `409` when the target workspace is locked by another active run, and
`POST /runs/{id}/approve-next` returns `409` if the workspace is locked when you try to
continue. In the **web UI**, the run detail has a *Locks* panel (lock state, with a manual
*Release* button behind a confirmation), and *New Run* surfaces a "workspace locked"
warning when the backend returns `409`.

> **Parallel runs:** do not point two runs at the same workspace at once -- the lock will
> refuse the second. To run sessions in parallel, give each its own
> [Git worktree](#parallel-sessions-with-git-worktrees) (a separate branch and directory)
> rather than sharing one working tree.

## Run queue and background worker

Claude Code and Codex runs can take a long time, so the API does not have to execute a run
inside the HTTP request. Instead it can **queue** the run and return immediately; a local
**background worker** then claims and executes queued jobs one at a time. This is a local
SQLite-backed queue for a single machine -- **not a distributed queue** or message broker.

A queue job tracks a run's execution (`id`, `run_id`, `status` of `QUEUED` / `RUNNING` /
`DONE` / `FAILED` / `CANCELLED`, `priority`, `attempts` / `max_attempts`, timestamps,
`last_error`). Lower priority numbers run first; ties break by oldest first. A run can have
only one active job at a time, and there is no automatic retry beyond `max_attempts`.

The worker executes through the same `RunService` path as a synchronous run, so the safety
checks, workspace locks, Git artifact capture, and prompt generation all still apply -- and
because it runs one job at a time and respects the workspace lock, queued runs for the same
workspace are serialized safely.

**Worker** (run it in its own terminal):

```
python -m autoprompt_runner.cli worker run                      # poll every 2s, Ctrl+C to stop
python -m autoprompt_runner.cli worker run --once               # execute one queued job, then exit
python -m autoprompt_runner.cli worker run --poll-interval-seconds 5
```

**Queued CLI run** -- create and enqueue without executing (prints the run id and job id):

```
python -m autoprompt_runner.cli run --project FactoryColony --prompt "Continue next task" --queued
python -m autoprompt_runner.cli queue list
python -m autoprompt_runner.cli queue cancel --run-id 12
```

`queue cancel` cancels a job only while it is still `QUEUED`; once a worker has started it
(`RUNNING`) the command returns a clean error -- **killing an in-progress process is not
implemented yet**.

**API.** `POST /runs` accepts a `queued` boolean (**default `true` for the API**): when
queued it creates the run, enqueues a job, and returns quickly with the run id and
`queue_status` / `queue_job_id`; with `queued=false` it keeps the existing synchronous
behavior. `GET /queue` lists jobs, `POST /queue/{run_id}/cancel` cancels a queued job
(`409` if it is already running), and `GET /runs/{id}` includes the run's queue status.

```
curl -X POST http://127.0.0.1:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Continue next task","project":"FactoryColony","queued":true}'
curl http://127.0.0.1:8000/queue
curl -X POST http://127.0.0.1:8000/queue/12/cancel
```

In the **web UI**, *New Run* has a "Queue run" checkbox (checked by default) and shows the
queued status after submitting; *Runs* shows a queue column; and the run detail has a
*Queue* panel (with *Cancel* for queued jobs and a note that running jobs cannot be killed
yet).

**Recommended local dev workflow** (four terminals):

1. start the API: `python -m uvicorn autoprompt_runner.api.app:app --reload`
2. start the frontend: `cd frontend && npm run dev`
3. start the worker: `python -m autoprompt_runner.cli worker run`
4. create a **queued** run from the web UI and watch the worker execute it.

## Cancelling and stopping runs

A queued, running, or waiting run can be cancelled. Cancellation moves the run to
`STOPPED`, releases its workspace lock, and records a `cancellation` artifact (and a
`run_cancellations` row of status `REQUESTED` -> `COMPLETED` / `FAILED`). What happens
depends on the run's state:

- **Queued** -> the queue job is cancelled so a worker never starts it.
- **Waiting for approval** -> the pending approval is rejected.
- **Running** -> a **best-effort** termination of the agent subprocess: the worker that
  launched it registers the process in a small in-memory registry and can stop it
  gracefully (terminate, then kill after a short grace period). That registry is **local
  to the worker process** and does not survive a restart, so a cancel issued from a
  different process (the API server or a separate CLI) cannot reach the worker's
  subprocess -- the run is still marked stopped and its lock released, but the external
  process may run to completion. A terminal run (`DONE` / `FAILED` / `STOPPED`) is a clean
  error.

CLI:

```
python -m autoprompt_runner.cli run cancel --run-id 12 --reason "User stopped from CLI"
python -m autoprompt_runner.cli queue cancel --run-id 12   # also uses the cancellation service
python -m autoprompt_runner.cli show-run --id 12           # shows the cancellation status/artifact
```

`run cancel` exits non-zero if the run is missing or already terminal.

API:

```
curl -X POST http://127.0.0.1:8000/runs/12/cancel \
  -H "Content-Type: application/json" -d '{"reason":"User stopped from web UI"}'
```

`POST /runs/{run_id}/cancel` returns the cancellation result, `404` if the run is missing,
and `409` if it is already terminal. `GET /runs/{run_id}` includes `cancellation_status`.

In the **web UI**, the run detail has a *Cancel* panel (optional reason, with a
confirmation, and the current cancellation status); the run list and the queue panel have
per-row *Cancel* buttons for cancellable runs. The UI notes that cancelling a running job
is best-effort.

## Git artifact capture

When a run's workspace is a Git repository, each step records read-only Git artifacts:

- `git_status_before` and `git_status_after` (porcelain status around the step),
- `git_diff` and `git_diff_stat` (what changed),
- `changed_files` (the list of changed/untracked paths),
- plus `runner_stdout` and `runner_stderr`.

Git capture is strictly **read-only**: the tool runs only `git status`, `git diff`,
and `git rev-parse`, and a denylist rejects any mutating subcommand (add, commit,
reset, checkout, clean, push, pull, merge, rebase, ...). It never stages, commits, or
otherwise changes the repository.

A non-Git workspace (or no workspace, e.g. the `mock` provider) is allowed: the run
does **not** fail, Git artifacts are skipped, and a compact `git_skipped` warning
artifact is recorded instead.

List a run's artifacts, then print one in full:

```
python -m autoprompt_runner.cli show-artifacts --run-id 1
python -m autoprompt_runner.cli show-artifacts --run-id 1 --type git_diff
python -m autoprompt_runner.cli show-artifact --id 1
```

`show-artifact` exits non-zero with a clean error if the artifact id does not exist.
`show-run` also prints each step's changed files and diff-stat summary when available.

## Search

As run history grows it gets hard to find a previous run, the log of an error, a
generated prompt, or which run touched a given file. **Search** scans the stored history
and returns compact, ranked matches across **runs**, **steps** (prompts and stdout /
stderr), and **artifacts** (including `changed_files` paths).

Search is **plain SQLite `LIKE`** over the local database -- no external search engine,
no full-text-search dependency, and no semantic / vector search. It is **case-insensitive**
and only ever reads **stored database content**: it never reads files from disk during a
search, and results carry small previews (a window around the match, or the first ~300
characters) rather than full artifact bodies, so large logs are never dumped and secrets
are not surfaced. Results default to **50** per query, are hard-capped at **200**, and
support `--limit` / `--offset` (API `limit` / `offset`) pagination.

What each scope matches:

- **runs** -> the root prompt, provider, and status (plus optional `status` / `provider`
  filters).
- **steps** -> the step prompt, stdout, stderr, and generated next prompt.
- **artifacts** -> the artifact type, content, and path (plus an optional `type` filter);
  `changed_files` artifacts make file-path searches work (find every run that changed a
  given file).

CLI (`search` command group):

```
python -m autoprompt_runner.cli search runs --query placement              # by prompt/provider/status
python -m autoprompt_runner.cli search runs --status FAILED --provider codex
python -m autoprompt_runner.cli search artifacts --query Traceback --type runner_stderr
python -m autoprompt_runner.cli search artifacts --query src/app.py        # which runs touched a file
python -m autoprompt_runner.cli search all --query "preview" --limit 25    # grouped runs/steps/artifacts
```

`search runs` accepts `--query` / `--status` / `--provider` / `--limit` / `--offset`,
`search artifacts` accepts `--query` / `--type` / `--limit` / `--offset`, and `search all`
accepts `--query` / `--limit` / `--offset` and prints grouped, compact results. An empty
query returns the most recent items (optionally narrowed by the filters).

API (`/search` route group; same local database):

```
curl "http://127.0.0.1:8000/search/runs?q=placement&status=FAILED&provider=codex"
curl "http://127.0.0.1:8000/search/artifacts?q=Traceback&type=runner_stderr"
curl "http://127.0.0.1:8000/search/all?q=preview&limit=25"
```

`GET /search/runs` (`q`, `status`, `provider`, `limit`, `offset`) and
`GET /search/artifacts` (`q`, `type`, `limit`, `offset`) return compact result lists;
`GET /search/all` (`q`, `limit`, `offset`) returns `{runs, steps, artifacts}` grouped.
Each result carries `match_field` / preview metadata, never full artifact content.

In the **web UI**, the *Search* section has a single query box with a target selector
(all / runs / artifacts), status / provider / artifact-type filters, and a 25 / 50 / 100
limit, plus loading / empty / error states. A run or step result is clickable and opens
the run detail; an artifact result is clickable and loads its full content in the inline
artifact viewer. The *Runs* section has a *Search* shortcut, and the run-detail artifact
list has a local "contains" box to narrow already-loaded artifacts without another request.

**Limitations.** Substring `LIKE` matching only (no ranking by relevance, stemming,
fuzzy matching, or natural-language queries); it searches only what has been stored in the
database (not the working tree); and previews are intentionally short.

## Run comparison

When two runs tackle the same task (a retry, a different provider, a tweaked prompt) it
helps to see exactly how they differ. **Comparison** loads two runs and reports the
differences across their stored content: run metadata (status / provider / created_at /
root prompt), step counts, exit codes and failed-step counts, the changed files (only in
A, only in B, common), the latest `git_diff_stat`, the latest generated next prompt, and
artifact counts by type -- plus a one-line summary.

Like search, comparison reads **only stored database content**: it never reads workspace
files from disk, never calls an external tool or diff engine, does no semantic diffing, and
never surfaces secret-file contents (only the changed-file *paths* recorded in the
`changed_files` artifacts). It is deterministic -- the result depends only on the stored
rows. Missing artifacts never fail a comparison: a run with no `changed_files` artifact
contributes an empty set and a compact warning, and diff-stat text is returned raw but
capped. Comparing a run with itself is rejected, as is a missing run id.

CLI (`compare runs`):

```
python -m autoprompt_runner.cli compare runs --run-a 12 --run-b 15
python -m autoprompt_runner.cli compare runs --run-a 12 --run-b 15 --show-prompts
python -m autoprompt_runner.cli compare runs --run-a 12 --run-b 15 --show-artifacts
```

The default output is compact (statuses, providers, step counts, failed steps, changed
files only-A / only-B / common, diff-stat previews, and latest next-prompt previews).
`--show-prompts` adds the full root and latest next-prompt text; `--show-artifacts` adds
the artifact count by type. A missing run exits non-zero ("not found"); comparing a run
with itself exits non-zero.

API (`/compare` route group; same local database):

```
curl "http://127.0.0.1:8000/compare/runs?run_a=12&run_b=15"
curl "http://127.0.0.1:8000/compare/runs?run_a=12&run_b=15&show_prompts=true&show_artifacts=true"
```

`GET /compare/runs` (`run_a`, `run_b`, `show_prompts=false`, `show_artifacts=true`) returns
a `RunComparisonResponse` with grouped run metadata, a step summary, the changed-files
comparison, diff-stat text, latest next-prompt previews, and artifact counts by type. It
returns `404` if either run is missing and `400` if `run_a == run_b`; full artifact content
is never returned.

In the **web UI**, the *Compare* section takes two run ids (typed or picked from a recent-
runs dropdown), optionally shows full prompts, and renders an A-vs-B summary table, the
changed-files breakdown (only A / only B / common), side-by-side diff-stat and next-prompt
blocks, and an artifact-count table -- with buttons to open either run's detail. The *Runs*
list has per-row **A** / **B** selectors and a **Compare A↔B** button, and a run's detail
view has **Use as compare A** / **Use as compare B** shortcuts.

**Limitations.** Compares only stored artifacts and step rows (not the working tree); no
semantic diff and no external diff engine; the changed-file comparison is set-based on the
recorded paths; and diff-stat text is raw and length-capped.

## Prompt chain history

A run is a chain: a root prompt drives the first step, whose result generates the next
prompt, which (after an approval) drives the next step, and so on. **Prompt chain history**
reconstructs that chain so you can see exactly how a run evolved -- the root prompt, each
step's prompt and generated next prompt, the approval decision, the provider result
(status / exit code), the artifacts captured, and the changed files -- as a single ordered
timeline.

The chain is built **only from stored run / step / approval / artifact data**: it never
reads workspace files from disk, never calls an external tool, does no semantic prompt
analysis, and never surfaces secret-file contents (only changed-file *paths* and artifact
*counts*). Nodes are ordered by loop index then step id. Missing artifacts or approvals
never fail chain creation -- they simply contribute empty counts / no approval status. The
chain is linear per run (there is no stored cross-run branching); previews are compact and
full prompt text is opt-in.

Each chain node carries: loop index, step id, status, exit code, provider, the prompt and
next-prompt previews (full text on request), the approval status, artifact counts by type,
a changed-files preview, and compact stdout/stderr previews. The chain summary adds the run
status, step count, approval count, failed-step count, total artifact count, and whether an
approval is pending.

CLI (`chain show`):

```
python -m autoprompt_runner.cli chain show --run-id 12
python -m autoprompt_runner.cli chain show --run-id 12 --full-prompts
python -m autoprompt_runner.cli chain show --run-id 12 --artifacts
python -m autoprompt_runner.cli chain show --run-id 12 --errors-only
```

The default output is a compact timeline (loop index, step id, status, exit code, approval
status, and prompt / next-prompt previews). `--full-prompts` prints the full prompt and
next-prompt text; `--artifacts` adds the per-node artifact counts (and changed files);
`--errors-only` shows just the failed nodes. A missing run exits non-zero ("not found").

API (`/chains` route group; same local database):

```
curl "http://127.0.0.1:8000/chains/runs/12"
curl "http://127.0.0.1:8000/chains/runs/12?full_prompts=true&include_artifacts=true&errors_only=false"
```

`GET /chains/runs/{run_id}` (`full_prompts=false`, `include_artifacts=true`,
`errors_only=false`) returns a `PromptChainResponse` with the chain summary and a
`chain_nodes` list. It returns `404` if the run is missing; full artifact content is never
returned (only counts, previews, and changed-file paths).

In the **web UI**, the run detail has a **Prompt chain** section that renders the chain as a
vertical timeline (no graph library, plain CSS): each node shows the loop index, step id,
status badge, exit code, approval status, prompt / next-prompt previews, artifact count,
changed files, and stdout/stderr previews, and expands to reveal the full prompt and next
prompt (with copy buttons) and the per-type artifact counts. A filter switches between
**all** / **failed only** / **waiting approval only**, and the *Runs* list has a per-row
**Chain** shortcut that opens the run detail.

**Limitations.** The chain is built from stored run / step / approval data only (not the
working tree); it is linear per run (no branch graph) and uses no graph-visualization
library; there is no semantic prompt analysis; and it performs no file-system reads.

## Failure recovery

When a run ends `FAILED`, **failure recovery** turns its stored failure context into a
focused, rule-generated recovery prompt and runs it as a **new linked run** -- without
losing the original run's history. The recovery prompt is built from *stored content only*
(the failed step's prompt and stdout/stderr previews, exit code, changed files, diff stat,
and safety warnings); it uses **no external AI API** and reads no workspace files. It asks
the agent to fix **only** the failed step, use stderr as the primary source, preserve the
intended behavior, rerun the relevant tests/command, and report remaining blockers -- and it
invents no file paths or test names (it only echoes stored signal), with compact previews so
no huge artifact content (or secret-file content) is included.

A recovery attempt has a status (`PROPOSED` → `APPROVED` / `REJECTED` → `EXECUTED` /
`FAILED`). Only `FAILED` runs can be recovered. Executing a recovery creates a new run that
**reuses the source run's provider / workspace / timeout / max-loops / approval / project
settings** and obeys the same safety checks, workspace locks, queue, and approval behavior;
the new run's id is linked back to the attempt (immediately, even when queued). The original
run's records are never mutated -- only the recovery linking metadata is stored.

CLI (`recovery` command group):

```
python -m autoprompt_runner.cli recovery propose --run-id 12            # only for a FAILED run
python -m autoprompt_runner.cli recovery propose --run-id 12 --show-prompt --reason "tests failing"
python -m autoprompt_runner.cli recovery approve --id 3                 # approve (does not execute)
python -m autoprompt_runner.cli recovery approve --id 3 --execute --queued
python -m autoprompt_runner.cli recovery reject  --id 3 --reason "Not needed"
python -m autoprompt_runner.cli recovery execute --id 3 --queued        # create + run the linked recovery run
python -m autoprompt_runner.cli recovery list --run-id 12
```

`propose` exits non-zero if the run is not `FAILED`; `execute` exits non-zero if the
recovery was rejected.

API (`/recovery` route group; same local database):

```
curl -X POST http://127.0.0.1:8000/recovery/runs/12/propose -H "Content-Type: application/json" -d '{"reason":"tests failing"}'
curl http://127.0.0.1:8000/recovery/runs/12
curl -X POST http://127.0.0.1:8000/recovery/3/approve
curl -X POST http://127.0.0.1:8000/recovery/3/reject -H "Content-Type: application/json" -d '{"reason":"Not needed"}'
curl -X POST http://127.0.0.1:8000/recovery/3/execute -H "Content-Type: application/json" -d '{"queued":true}'
curl http://127.0.0.1:8000/recovery
```

`POST /recovery/runs/{run_id}/propose` returns `400` if the run is not `FAILED`; a missing
run or recovery returns `404`; `POST /recovery/{id}/execute` returns `409` if the recovery
was rejected and otherwise returns the attempt with its linked `recovery_run_id`.

In the **web UI**, the run detail has a **Recovery** section (shown only when the run is
`FAILED` or already has recovery attempts): a *Propose recovery* button, each attempt's
status badge and recovery-prompt preview (with *Show full prompt*), *Approve* / *Reject* /
*Execute* / *Execute queued* actions, and a link to open the linked recovery run. The *Runs*
list shows a *Recover* shortcut on failed rows that opens the run detail.

**Limitations.** Recovery uses **stored failure context only** (not the working tree); the
recovery prompt is rule-based (no semantic analysis, no external AI); it creates a **new
linked run** rather than rerunning the original; and only `FAILED` runs are recoverable.

## Provider settings (provider profiles)

A **provider profile** configures how a provider is invoked -- its `command` executable, a
`default_timeout_seconds`, and optional space-separated `default_args` -- without hardcoding
those in the runners. A profile has a `name`, a `type` (`mock`, `claude-code`, or `codex`),
and an `enabled` flag, and its **name may differ from its type**, so you can keep several
configurations for one runner (for example a `claude-fast` profile of type `claude-code`).

Profiles are stored in the local SQLite database (`provider_profiles` table). **They never
store secrets** -- only non-secret command/argument settings; AutoPromptRunner reads no
credentials from them. **Availability** is checked by *command discovery only*
(`shutil.which`): it reports whether an external command is on `PATH` and **never executes
the real Claude Code or Codex CLI**, so it is safe to check anywhere (mock is always
available). Seed the built-in defaults with `provider seed`:

| Name | Type | Command | Default timeout | Enabled |
| --- | --- | --- | --- | --- |
| `mock` | `mock` | `mock` | 30 | yes |
| `claude-code` | `claude-code` | `claude` | 1800 | yes |
| `codex` | `codex` | `codex` | 1800 | yes |

Seeding never overwrites a profile you have modified unless you pass `--force`.

**Provider resolution at run time.** A run's `--provider` (or a project's default provider)
is resolved as: an explicit **provider profile name** → the project default → the config
default → the built-in `mock` fallback. A disabled profile is rejected with a clean error,
and an external profile whose command is unavailable is rejected **before** execution
(mock is exempt); an explicit `--timeout-seconds` overrides the profile's default timeout.
The built-in names `mock` / `claude-code` / `codex` keep working whether or not profiles are
seeded.

CLI (`provider` command group):

```
python -m autoprompt_runner.cli provider seed
python -m autoprompt_runner.cli provider list
python -m autoprompt_runner.cli provider show --name claude-code
# a custom claude profile (note: use --default-args="--flag" form for values starting with -)
python -m autoprompt_runner.cli provider add --name claude-fast --type claude-code --command claude --timeout-seconds 1200
# a custom codex profile
python -m autoprompt_runner.cli provider add --name codex-fast --type codex --command codex --timeout-seconds 900
python -m autoprompt_runner.cli provider update --name claude-fast --timeout-seconds 1800
python -m autoprompt_runner.cli provider enable --name claude-fast
python -m autoprompt_runner.cli provider disable --name claude-fast
python -m autoprompt_runner.cli provider check --name claude-code   # exits non-zero if unavailable
python -m autoprompt_runner.cli provider delete --name claude-fast  # removes the profile only
```

Then run against a profile by name (the default `claude-code` profile, or a custom one):

```
python -m autoprompt_runner.cli run --project FactoryColony --provider claude-code --prompt "Continue next task"
python -m autoprompt_runner.cli run --project FactoryColony --provider claude-fast --prompt "Continue next task"
```

API (`/providers` route group; same local database):

```
curl -X POST http://127.0.0.1:8000/providers/seed
curl http://127.0.0.1:8000/providers
curl -X POST http://127.0.0.1:8000/providers \
  -H "Content-Type: application/json" \
  -d '{"name":"claude-fast","type":"claude-code","command":"claude","default_timeout_seconds":1200}'
curl http://127.0.0.1:8000/providers/claude-fast
curl -X PATCH http://127.0.0.1:8000/providers/claude-fast \
  -H "Content-Type: application/json" -d '{"default_timeout_seconds":1800}'
curl -X POST http://127.0.0.1:8000/providers/claude-fast/disable
curl -X POST http://127.0.0.1:8000/providers/claude-fast/enable
curl http://127.0.0.1:8000/providers/claude-code/check
curl -X DELETE http://127.0.0.1:8000/providers/claude-fast
```

`GET /providers` returns each profile with a computed `available` flag,
`GET /providers/{name}/check` returns an availability result, and `POST /runs` rejects a
disabled provider (400) or an unavailable external provider (400) before creating the run.

In the **web UI**, the *Providers* section has a provider form (create / edit: name, type,
command, default timeout, default args, enabled), a *Provider availability* health panel
(command-discovery only -- no agent is executed), and a list with per-row **Check** /
**Enable** / **Disable** / **Edit** / **Delete** (with confirmation) actions plus a *Seed
defaults* button. The *New Run* provider dropdown is populated from the provider profiles,
marking disabled (unselectable) and unavailable profiles.

> **Never store secrets in a provider profile.** The `command` and `default_args` are
> non-secret invocation settings only; credentials belong with the agent CLI's own auth.

## Export and import

AutoPromptRunner can export its local data to a portable **JSON file** and import it back
into another local database -- useful for backup, moving between machines, or sharing a run
history. Export covers project profiles, provider profiles, templates, and run history
(runs, steps, approvals, artifacts, recovery attempts). It reads **only stored database
content** -- never workspace files, environment variables, or config files -- and uses the
Python standard library only (no cloud sync).

**Export format** -- a single self-describing JSON object:

```json
{
  "format": "autoprompt-runner-export",
  "version": 1,
  "exported_at": "...",
  "source": { "app": "AutoPromptRunner", "schema_version": 1 },
  "data": {
    "projects": [], "provider_profiles": [], "templates": [],
    "runs": [], "steps": [], "approvals": [], "artifacts": [], "recovery_attempts": []
  }
}
```

**Redaction (best-effort, on by default).** Exports do **not** include environment variables
or config files, and never read workspace files. An artifact whose *path* or *type* looks
secret-like (`.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa`, `id_dsa`, `id_ed25519`,
`secrets.*`, `credentials.*`, `service-account*.json`, `*.p12`, `*.pfx`, or a
secret/credential/token type) has its content replaced with
`[REDACTED_BY_AUTOPROMPT_RUNNER_EXPORT]` and flagged. Redaction is best-effort, **not a
secrecy guarantee** -- exports still include run prompts and step stdout/stderr, so review
before sharing. Pass `--no-redact` to disable redaction, or `--no-artifact-content` to
export artifact metadata without any content.

**Import modes.** Import **never deletes existing data**:

- `merge` (default) -- add imported runs/steps/artifacts/recoveries as new rows; an existing
  project / provider / template (matched by name) is kept (not overwritten).
- `skip_existing` -- same, and additionally skips a run that already exists (matched by
  `created_at` + root prompt), so re-importing the same file does not duplicate runs.
- `replace_templates_only` -- like `merge`, but a template whose name already exists is
  overwritten by the imported one (providers/projects are still never overwritten).

Imported rows get **new local ids**; run → step → approval → artifact → recovery
relationships are preserved by remapping the ids. The payload's `format` and `version` are
validated before import, and an unknown major version is rejected.

CLI:

```
python -m autoprompt_runner.cli export data --output autoprompt-export.json
python -m autoprompt_runner.cli export data --output one-run.json --run-id 12 --no-artifact-content
python -m autoprompt_runner.cli export data --output factory.json --project FactoryColony   # only that project's runs
python -m autoprompt_runner.cli export summary --input autoprompt-export.json
python -m autoprompt_runner.cli import data --input autoprompt-export.json --mode merge
python -m autoprompt_runner.cli import data --input autoprompt-export.json --mode skip_existing
```

`export data` flags: `--run-id` (repeatable), `--project` (repeatable), `--no-projects`,
`--no-providers`, `--no-templates`, `--no-artifacts`, `--no-recoveries`,
`--no-artifact-content`, `--no-redact`. `import data` exits non-zero on an invalid file.

API (`/export-import` route group; no server-side file is written):

```
curl -X POST http://127.0.0.1:8000/export-import/export \
  -H "Content-Type: application/json" \
  -d '{"include_artifacts":true,"redact_sensitive":true,"run_ids":[],"project_names":[]}'
curl -X POST http://127.0.0.1:8000/export-import/summary -H "Content-Type: application/json" -d '{"payload": { ... }}'
curl -X POST http://127.0.0.1:8000/export-import/import \
  -H "Content-Type: application/json" -d '{"payload": { ... }, "mode": "merge"}'
```

`POST /export-import/export` returns the JSON payload; `POST /export-import/import` validates
and applies it (returning a compact import summary, `400` on an invalid payload or unknown
version); `POST /export-import/summary` returns counts without importing.

In the **web UI**, the *Export / Import* section has an export form (include-toggles for
projects / providers / templates / runs / artifacts / recoveries, plus artifact-content and
redact-sensitive toggles) that downloads the JSON file, and an import form (file picker,
mode selector, *Preview summary*, *Import*) that shows the result -- with explicit warnings
that exports may include prompts / output / artifact content, that redaction is best-effort,
and not to import untrusted files without review.

**Limitations.** No cloud sync; **no secrecy guarantee** (redaction is best-effort and run
prompts / output are exported); no workspace file export; and import is **non-destructive**
(it never deletes existing runs and never overwrites providers/projects, nor templates
except in `replace_templates_only`).

## Claude Code provider

The `claude-code` provider runs the real Claude Code CLI as a subprocess.

- **Requirement:** the Claude Code CLI must already be installed and authenticated.
  AutoPromptRunner does not install it and never handles API keys.
- **Example:**

  ```
  python -m autoprompt_runner.cli run \
    --prompt "Review this project and suggest the next smallest implementation task" \
    --provider claude-code --workspace /path/to/project --max-loops 1
  ```

- **Workspace:** `--workspace` is required for `claude-code` and must be an existing
  directory (or supplied via a project's `repo_path`).
- **Timeout:** `--timeout-seconds` (default 1800, >= 1) bounds the subprocess; timeouts
  and a missing `claude` command are captured as clean non-zero results.
- **Approval gate:** identical to the mock provider.
- **Safety warning:** Claude Code may create, modify, or delete files inside the
  workspace. Point `--workspace` only at a project you intend Claude Code to change,
  ideally one tracked in version control.

## Codex provider

The `codex` provider runs the Codex CLI as a subprocess, using the same provider adapter
model as `claude-code`. All Codex-specific CLI details are isolated inside `CodexRunner`.

- **Requirement:** the Codex CLI must already be installed and authenticated.
  AutoPromptRunner does not install it and never handles API keys.
- **Example:**

  ```
  python -m autoprompt_runner.cli run \
    --prompt "Review this project and suggest the next smallest implementation task" \
    --provider codex --workspace /path/to/project --max-loops 1
  ```

- **Workspace:** `--workspace` is required for `codex` and must be an existing directory
  (or supplied via a project's `repo_path`). Codex runs in non-interactive execution
  mode (`codex exec`) inside that directory.
- **Timeout:** `--timeout-seconds` (default 1800, >= 1) bounds the subprocess; timeouts
  and a missing `codex` command are captured as clean non-zero results.
- **Approval gate:** identical to the mock and claude-code providers.
- **Safety warning:** Codex may create, modify, or delete files inside the workspace.
  Point `--workspace` only at a project you intend Codex to change, ideally one tracked
  in version control.

## HTTP API (FastAPI)

An optional FastAPI backend exposes the same run/project operations over HTTP, using
the **same local SQLite database** as the CLI. There is no authentication, no
websocket / live-log streaming, and **no frontend** yet.

Install the API extra and start the server:

```
pip install -e ".[api]"
python -m uvicorn autoprompt_runner.api.app:app --reload
```

Health check:

```
curl http://127.0.0.1:8000/health
# {"status": "ok", "service": "AutoPromptRunner"}
```

Projects (profile only on delete -- files on disk are never deleted):

```
curl -X POST http://127.0.0.1:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"name":"FactoryColony","repo_path":"/path/to/FactoryColony","default_provider":"claude-code","default_max_loops":5,"require_approval":true,"timeout_seconds":1800}'
curl http://127.0.0.1:8000/projects
curl http://127.0.0.1:8000/projects/FactoryColony
curl -X POST http://127.0.0.1:8000/projects/FactoryColony/default
curl -X DELETE http://127.0.0.1:8000/projects/FactoryColony
```

Runs and approvals (project/default resolution and validation match the CLI):

```
curl -X POST http://127.0.0.1:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Continue next task","project":"FactoryColony","max_loops":3}'
curl http://127.0.0.1:8000/runs
curl http://127.0.0.1:8000/runs/1                      # detail: steps, pending approval, artifacts
curl -X POST http://127.0.0.1:8000/runs/1/approve-next  # execute the pending next step
curl -X POST http://127.0.0.1:8000/runs/1/reject-next   # stop the run
curl "http://127.0.0.1:8000/runs/1/artifacts?type=git_diff"
curl http://127.0.0.1:8000/artifacts/1
```

Interactive docs are served at `/docs` (Swagger UI) and `/redoc`. Errors use standard
HTTP status codes (400 invalid request, 404 missing project/run/artifact, 409 invalid
run state) and never leak stack traces or secrets. A frontend is not implemented yet.

## Web UI (frontend)

A minimal React + Vite + TypeScript **dashboard** lives in `frontend/` (see
[frontend/README.md](frontend/README.md)). It is a thin, **local-first and unauthenticated**
shell over the HTTP API -- no router, no state library, no UI framework. A left **sidebar**
navigates between sections with simple local state (no routing): **Overview**,
**Projects**, **Templates**, **Worktrees**, **Providers**, **New Run**, **Runs**, **Search**,
**Compare**, **Queue**, **Export / Import**, and -- once a run is open -- **Run Detail**. The
active section is highlighted.

- **Overview** shows compact cards: backend health, recent-run count, queued / running
  jobs, failed runs, the default and selected project, and a reminder to start a worker
  when jobs are queued.
- **New Run** offers two explicit modes (direct prompt or from a template), a worktree
  selector, and a live "resolved execution config" summary (project, worktree, provider,
  workspace, max loops, timeout, approval mode, queued vs run-now) before you submit.
- **Runs** lists runs with status / provider filters and colored status badges; click a
  row to open the detail, or cancel a cancellable run inline.

Run the backend, a worker, and the frontend (the `scripts/` helpers wrap these):

```
# 1) Backend API
pip install -e ".[api]"
scripts/dev_api.sh            # or: python -m uvicorn autoprompt_runner.api.app:app --reload

# 2) Background worker (executes queued runs)
scripts/dev_worker.sh         # or: python -m autoprompt_runner.cli worker run

# 3) Frontend dev server
scripts/dev_frontend.sh       # or: cd frontend && npm install && npm run dev
```

Open http://localhost:5173. The UI calls the API at `http://localhost:8000` by default;
override it with `VITE_API_BASE_URL`. A production build is `npm run build` (outputs
`frontend/dist/`); the backend enables permissive CORS for local development.

**Common web workflow:**

1. Start the API, a worker, and the frontend (above).
2. In **Projects**, create and (optionally) set a default project profile.
3. In **New Run**, choose direct or template mode, review the resolved config, and submit.
4. The run opens in **Run Detail**; inspect its steps, changed files, diff stat, and
   artifacts.
5. **Approve** / **Reject** a pending next prompt, or **Cancel** a queued / running run.
6. **Queue** shows queued / running / done jobs -- cancel queued ones (running
   cancellation is best-effort).

### Run detail and artifact review

Selecting a run opens a dense detail view: a summary, the **Steps** list (status, exit
code, timestamps, and stdout/stderr previews), a **Prompt chain** timeline (see
[Prompt chain history](#prompt-chain-history)), **Changed files** and **Diff stat**
panels, an **Artifacts** browser, and an **Artifact viewer**.

- **Artifacts** lists every captured artifact (`git_status_before` / `git_status_after`,
  `git_diff`, `git_diff_stat`, `changed_files`, `runner_stdout`, `runner_stderr`) with a
  type filter.
- **Artifact viewer** shows the selected artifact's full content in a scrollable,
  monospace, whitespace-preserving block with a copy-to-clipboard button (it does not
  crash on large content).

Diff review workflow: open a run, read **Changed files** and **Diff stat** for a quick
overview, then select the `git_diff` artifact to read the full diff in the viewer.

Approval workflow: when a run is `WAITING_APPROVAL`, the **Approval** panel shows the
pending next prompt (toggle **Show full next prompt** for the untruncated text).
**Approve** runs the next step and **Reject** stops the run; either action reloads the
run detail, the run list, and the artifacts.

### Live logs (polling)

The **Logs** panel in the run detail polls `GET /runs/{id}/logs` every
2 seconds while the run is `RUNNING` or `WAITING_APPROVAL`, and stops once the run is
`DONE`, `FAILED`, or `STOPPED`. It shows the run status, the latest step id, and the
latest stdout/stderr in scrollable monospace blocks, with **Refresh** and
**Pause/Resume** controls; when polling detects a terminal status the full run detail
reloads.

This is **polling, not a true stream**: because each runner writes its output when its
step finishes, stdout/stderr update only after each step completes (not character by
character). True streaming (SSE or WebSocket) is planned for a future step.

## Runner Providers

| Provider | Class | Status | Description |
| --- | --- | --- | --- |
| `mock` | `MockRunner` | Available | Deterministic, offline runner used for tests and dry runs. Default provider. |
| `claude-code` | `ClaudeCodeRunner` | Available | Runs the Claude Code CLI as a subprocess inside a workspace. |
| `codex` | `CodexRunner` | Available | Runs the Codex CLI as a subprocess inside a workspace. |

## Tests

Standard library only; the Claude Code / Codex subprocesses are faked and Git artifacts
use temporary repositories, so **no real `claude` or `codex` is needed** and no network is
used (the FastAPI tests run in-process via the test client). End-to-end flows are covered
by `tests/test_e2e_cli_flow.py` and `tests/test_e2e_api_flow.py`.

```
python -m unittest discover -s tests -v     # or: python -m pytest
scripts/check_all.sh                        # tests + config validate + frontend build
```

## Troubleshooting

- **`ModuleNotFoundError: fastapi`** -> install the API extra: `pip install -e ".[api]"`
  (the CLI core itself needs no third-party packages).
- **`config validate` fails** -> run `config show` to see the effective values; common
  causes are `max_loops`/`timeout_seconds` above their hard limits or an empty `db_path`.
- **`run` says the provider is unsupported** -> use `mock`, `claude-code`, or `codex`.
- **`claude-code` / `codex` runs end FAILED with "command not found"** -> the CLI is not
  installed or not on `PATH`; AutoPromptRunner never installs it and never handles keys.
- **A run is `WAITING_APPROVAL` and nothing happens** -> approve or reject it
  (`approve-next` / `reject-next`, or the run detail in the web UI).
- **`POST /runs` returns `409`** -> the workspace is locked by another active run; use a
  separate Git worktree for parallel sessions, or release a stale lock (`locks release`).
- **A queued run never executes** -> start a worker (`worker run`); queued runs do nothing
  until a worker claims them.
- **Frontend cannot reach the API** -> start the backend (`scripts/dev_api.sh`) and/or set
  `VITE_API_BASE_URL` (e.g. `VITE_API_BASE_URL=http://127.0.0.1:9000 npm run dev`).

### Setup / packaging troubleshooting

- **`python` not found** -> install Python >= 3.11 and ensure it is on `PATH`; run
  `scripts/doctor.sh` to confirm. On some systems the command is `python3`.
- **`npm` / `node` not found** -> install Node.js (includes npm). It is only needed to
  build or run the web UI; the CLI and API work without it. `scripts/doctor.sh` reports it
  as a warning, not a failure.
- **Claude Code (`claude`) not installed** -> only the `claude-code` provider needs it;
  `mock` (and the rest of the tool) works without it. Availability is reported by
  `provider check --name claude-code` and `scripts/doctor.sh`.
- **Codex (`codex`) not installed** -> only the `codex` provider needs it; `mock` works
  without it. Check with `provider check --name codex`.
- **DB path issues** -> the database defaults to `.autoprompt/autoprompt.db` (override with
  `--db-path` or `AUTOPROMPT_DB_PATH` / `[storage] db_path`); the parent directory is created
  automatically. Use `python -m autoprompt_runner.cli config show` to see the effective path.
- **`python -m build` missing in `package_release.sh`** -> install it with
  `python -m pip install build`; the script prints this instruction and still assembles the
  frontend bundle.

## v0.1 checklist

- [x] CLI works (init-db, project/template/run/approve/reject/list/show/artifacts/safety-check)
- [x] SQLite persistence
- [x] MockRunner works; ClaudeCodeRunner / CodexRunner fail safely when the CLI is absent
- [x] Prompt loop and approval gate
- [x] Git artifact capture and the safety checks
- [x] Project profiles and prompt templates
- [x] Git worktrees and workspace locks
- [x] Local queue + background worker, and run cancellation
- [x] Search across runs, logs, prompts, and artifacts (CLI / API / web UI)
- [x] Compare two runs (CLI / API / web UI)
- [x] Prompt chain history view (CLI / API / web UI)
- [x] Provider profiles / settings management (CLI / API / web UI)
- [x] Failure recovery workflow (CLI / API / web UI)
- [x] Export / import of data (CLI / API / web UI)
- [x] Config file / environment overrides (`config show` / `validate` / `init`)
- [x] FastAPI backend and the React/Vite frontend build
- [x] Local install / packaging scripts (`setup_local` / `check_all` / `doctor` / `package_release`)
- [x] End-to-end CLI and API flow tests pass

## Project Documents

- [PROJECT.md](PROJECT.md) - product specification, architecture, and state machine.
- [AGENTS.md](AGENTS.md) - operating rules for coding agents working in this repository.
