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

- Python >= 3.11
- No third-party runtime dependencies (standard library only)
- For the `claude-code` provider: the Claude Code CLI installed and authenticated
- For Git artifact capture: the `git` CLI (optional; skipped when absent or non-repo)

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

A minimal React + Vite + TypeScript single-page UI lives in `frontend/`. It is a thin
shell over the HTTP API -- health, projects (list/create), runs (list/create/detail),
and approve/reject. There is no router, state library, or UI framework, and no live-log
streaming or auth yet.

Run the backend and the frontend in two terminals:

```
# 1) Backend API (terminal 1)
pip install -e ".[api]"
python -m uvicorn autoprompt_runner.api.app:app --reload   # http://localhost:8000

# 2) Frontend dev server (terminal 2)
cd frontend
npm install
npm run dev                                                # http://localhost:5173
```

Open http://localhost:5173. The UI calls the API at `http://localhost:8000` by default;
override it with the `VITE_API_BASE_URL` environment variable. A production build is
`npm run build` (outputs `frontend/dist/`). The backend enables permissive CORS for
local development.

Example local workflow:

1. Start the FastAPI backend.
2. Start the frontend dev server and open it in a browser.
3. Create a project profile (name, repo path, provider, limits) in **New Project**.
4. Start a run in **New Run** (pick a project, or leave it blank for the default).
5. Open the run in **Runs**, review its steps, and **Approve** or **Reject** the pending
   next prompt.

### Run detail and artifact review

Selecting a run opens a dense detail view: a summary, the **Steps** list (status, exit
code, timestamps, and stdout/stderr previews), **Changed files** and **Diff stat**
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

The **Live log** panel near the top of the run detail polls `GET /runs/{id}/logs` every
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

Standard library only; the Claude Code subprocess is faked and Git artifacts use
temporary repositories, so no real `claude` is needed:

```
python -m unittest discover -s tests -v
```

## Project Documents

- [PROJECT.md](PROJECT.md) - product specification, architecture, and state machine.
- [AGENTS.md](AGENTS.md) - operating rules for coding agents working in this repository.
