# AutoPromptRunner

## One-Line Description

AutoPromptRunner is a local-first **prompt queue controller for the Claude Code desktop app**: it lets users prepare multiple prompts, bind them to a Claude Code app target/session, inject prompts into the app one by one, and manage completion, pause, skip, retry, and cancellation.

## Product Focus

AutoPromptRunner is a local-first prompt queue controller for the Claude Code desktop app. It lets users prepare multiple prompts, bind them to a Claude Code app target/session, inject prompts into the app one by one, and manage completion, pause, skip, retry, and cancellation.

Clarifications:

- AutoPromptRunner **does not replace Claude Code** — it drives the Claude Code app.
- AutoPromptRunner **does not execute prompts through the Claude Code CLI by default**; the primary workflow is injecting prompts into the Claude Code **app**.
- AutoPromptRunner controls **prompt queueing and prompt injection** into the Claude Code app.
- The CLI-based providers (`mock`, `claude-code`, `codex`) and the run/loop engine **remain as secondary / fallback providers** and history, but they are no longer the primary product.
- The primary workflow is **Claude Code app prompt injection**: the user focuses the correct Claude Code session/pane input, AutoPromptRunner injects the current queued prompt (clipboard + paste), the user marks it complete, and the next prompt is injected.

The app-queue model (current primary):

1. The user registers an **app target** (a specific Claude Code app session/pane).
2. The user creates a **prompt queue** bound to that target and adds prompts (Prompt#34, #35, #36, …).
3. The user focuses the correct Claude Code input and triggers **inject-current**; the prompt is copied to the clipboard and (when a keyboard backend is available) pasted/submitted into the active window.
4. The prompt becomes `WAITING_COMPLETION`; the user marks it complete, and the next prompt becomes ready. Injection is always explicit — never automatic.

The sections below describe the original CLI run/loop engine, which is retained as the secondary/fallback path.

## Original CLI run engine (secondary)

AutoPromptRunner can also take a command from a web UI or CLI, send a prompt to a coding agent such as Claude Code or Codex, collect the result, generate the next prompt, and optionally run the next step after approval.

## Problem Statement

Driving a coding agent across a multi-step task today means a human copies a prompt into a CLI, waits, reads the output, decides what to ask next, and pastes the next prompt. This manual loop is slow, hard to reproduce, and leaves no structured record of what was run or why. There is also no consistent gate between "the agent proposed a next step" and "the next step actually executed," so either the human babysits every iteration or unattended runs proceed without any checkpoint. AutoPromptRunner closes this loop: it persists each command, prompt, agent invocation, and result, derives the next prompt automatically, and enforces an explicit approval or auto-run decision and a hard loop bound before continuing.

## Target Users

- Developers who run coding agents (Claude Code, Codex) on iterative tasks and want the prompt/result/next-prompt cycle automated and recorded.
- Engineers building internal automation that needs an auditable history of agent invocations, their stdout/stderr/exit codes, and the prompts that produced them.
- Teams that want unattended agent loops but require a controllable approval gate and a maximum iteration count to prevent runaway execution.

## Core Use Case

A user starts a run against a project with an initial instruction, for example "add input validation to the signup endpoint and its tests." AutoPromptRunner sends that prompt to Claude Code, captures the output and exit code, summarizes what changed, and generates a follow-up prompt such as "run the test suite and fix any failures introduced by the validation change." The user reviews the proposed next prompt and approves it, or the run was configured to auto-run. The cycle repeats until the task is reported done, a step fails, the user stops it, or the configured `maxLoops` is reached.

## Core Workflow

User command -> create run -> execute agent prompt -> collect stdout/stderr/result -> summarize result -> generate next prompt -> wait for approval or auto-run -> repeat until done, failed, stopped, or `maxLoops` reached.

1. **User command** — a command arrives from the CLI or web UI specifying the project, initial prompt, provider, and run options (`maxLoops`, auto-run flag).
2. **Create run** — a `runs` row is persisted with status `CREATED`.
3. **Execute agent prompt** — the selected runner invokes the agent with the current prompt.
4. **Collect stdout/stderr/result** — stdout, stderr, exit code, `started_at`, and `finished_at` are captured into a `steps` row and any produced files into `artifacts`.
5. **Summarize result** — the collected output is condensed into a short summary used for context and display.
6. **Generate next prompt** — the summary and result drive generation of the next prompt.
7. **Wait for approval or auto-run** — an `approvals` record gates the next step unless auto-run is explicitly enabled.
8. **Repeat** — steps 3–7 loop until a terminal condition (`DONE`, `FAILED`, `STOPPED`) or `maxLoops`.

## MVP Scope

The MVP is implemented in **Python** with **SQLite** for persistence and is **CLI-first**. It supports:

- Creating and listing projects and runs from the CLI.
- A single working runner, the **subprocess-based Claude Code runner**, invoked via the Claude Code CLI.
- The full state machine including result collection, next-prompt generation, the approval gate, and the `maxLoops` bound.
- Persistence of projects, runs, steps, artifacts, agent providers, and approvals in SQLite.
- A `MockRunner` so the loop and state machine can be tested without an external process.

**FastAPI** (the HTTP service) and a **React or Next.js** web UI are designed for but added after the CLI loop is stable.

## Out of Scope for MVP

- The FastAPI HTTP service and the React/Next.js web UI (designed for, delivered later).
- Runner providers other than Claude Code and Mock (Codex and Shell runners come later).
- Parallel or concurrent runs; the MVP executes one step at a time per run.
- Distributed execution, queue brokers, or multi-machine workers.
- Authentication, multi-tenant access control, and role management.
- Advanced observability (metrics dashboards, tracing) beyond stored stdout/stderr/exit codes and summaries.

## Architecture Overview

The system is a single Python process for the MVP, organized into layers:

- **Entry layer** — CLI commands (FastAPI endpoints added later) that create runs and issue control actions (approve, stop).
- **Orchestrator** — drives the state machine, advancing a run from state to state, enforcing `maxLoops`, and recording transitions.
- **Runner providers** — a common interface with pluggable implementations; the MVP ships `ClaudeCodeRunner` (subprocess to the Claude Code CLI) and `MockRunner`.
- **Result collector and summarizer** — captures stdout/stderr/exit code/timestamps from the runner and produces a short summary.
- **Next-prompt generator** — turns the summary and result into the next prompt.
- **Approval service** — creates and resolves the approval gate; honors the auto-run flag.
- **Persistence** — **SQLite** accessed through a thin data-access layer storing projects, runs, steps, artifacts, agent providers, and approvals.

Later additions: a **FastAPI** service exposing the same orchestrator over HTTP, and a **React or Next.js** front end consuming that API.

## Main Components

- **CLI** — parses user commands, creates runs, lists state, submits approvals and stop requests.
- **Orchestrator / State Machine** — owns run lifecycle and transitions; the single place that decides the next state.
- **Runner interface + providers** — `ClaudeCodeRunner`, `CodexRunner`, `ShellRunner`, `MockRunner`; each returns stdout, stderr, exit code, `started_at`, `finished_at`.
- **Result Collector** — persists a `steps` row and `artifacts` from a runner result.
- **Summarizer** — produces a concise summary of a step result.
- **Next-Prompt Generator** — produces the next prompt from the prior result and summary.
- **Approval Service** — creates approval records and resolves them (approved/rejected), or auto-resolves them when auto-run is set.
- **Persistence Layer** — SQLite schema and queries for all entities.
- **(Later) FastAPI app** and **(Later) React/Next.js UI**.

## State Machine

States advance in this order:

`CREATED -> QUEUED -> RUNNING -> COLLECTING_RESULT -> GENERATING_NEXT_PROMPT -> WAITING_APPROVAL -> RUNNING_NEXT -> DONE / FAILED / STOPPED`

Transitions:

- **CREATED** — run row persisted with its project, initial prompt, provider, `maxLoops`, and auto-run flag. Transitions to `QUEUED`.
- **QUEUED** — run is accepted for execution. Transitions to `RUNNING`.
- **RUNNING** — the runner executes the current prompt against the agent. On a captured result, transitions to `COLLECTING_RESULT`; on a runner/launch error, transitions to `FAILED`.
- **COLLECTING_RESULT** — stdout, stderr, exit code, and timestamps are stored as a `steps` row with `artifacts`. A nonzero exit treated as fatal transitions to `FAILED`; otherwise transitions to `GENERATING_NEXT_PROMPT`. If the agent reports the task complete, transitions to `DONE`.
- **GENERATING_NEXT_PROMPT** — the summary and result produce the next prompt. Transitions to `WAITING_APPROVAL`.
- **WAITING_APPROVAL** — an `approvals` record gates the next prompt. If auto-run is enabled, the approval is auto-resolved and the run transitions to `RUNNING_NEXT`. On explicit approval, transitions to `RUNNING_NEXT`. On rejection or a stop request, transitions to `STOPPED`.
- **RUNNING_NEXT** — increments the loop counter and re-enters execution. If the loop counter has reached `maxLoops`, transitions to `STOPPED`; otherwise loops back to `RUNNING` with the new prompt.

Terminal states:

- **DONE** — the task was reported complete; no further steps run.
- **FAILED** — a step failed fatally (runner error or fatal nonzero exit); the loop ends.
- **STOPPED** — the user rejected/stopped the run, or `maxLoops` was reached before completion.

The loop from `RUNNING` through `RUNNING_NEXT` is bounded by `maxLoops`: the run cannot execute more than `maxLoops` agent invocations, after which it ends in `STOPPED`.

## Data Model Draft

Relationships: a project has many runs; a run has many steps; a step has many artifacts; an approval gates a step. Agent providers are referenced by runs and steps to record which runner executed them.

**projects**

| Field | Type | Notes |
| --- | --- | --- |
| id | integer PK | |
| name | text | project name |
| repo_path | text | working directory for runners |
| created_at | datetime | |

**runs**

| Field | Type | Notes |
| --- | --- | --- |
| id | integer PK | |
| project_id | integer FK -> projects.id | run belongs to a project |
| provider_id | integer FK -> agent_providers.id | selected runner |
| initial_prompt | text | first prompt |
| status | text | current state machine state |
| auto_run | boolean | skip manual approval when true |
| max_loops | integer | loop bound |
| loop_count | integer | invocations executed so far |
| created_at | datetime | |
| updated_at | datetime | |

**steps**

| Field | Type | Notes |
| --- | --- | --- |
| id | integer PK | |
| run_id | integer FK -> runs.id | step belongs to a run |
| provider_id | integer FK -> agent_providers.id | runner used |
| index | integer | ordinal within the run |
| prompt | text | prompt sent to the agent |
| stdout | text | captured stdout |
| stderr | text | captured stderr |
| exit_code | integer | runner exit code |
| summary | text | condensed result summary |
| next_prompt | text | generated next prompt |
| started_at | datetime | |
| finished_at | datetime | |

**artifacts**

| Field | Type | Notes |
| --- | --- | --- |
| id | integer PK | |
| step_id | integer FK -> steps.id | artifact belongs to a step |
| kind | text | file, diff, log, etc. |
| path | text | location on disk |
| created_at | datetime | |

**agent_providers**

| Field | Type | Notes |
| --- | --- | --- |
| id | integer PK | |
| name | text | e.g. claude_code, codex, shell, mock |
| runner_class | text | implementation identifier |
| config_json | text | provider-specific settings |

**approvals**

| Field | Type | Notes |
| --- | --- | --- |
| id | integer PK | |
| step_id | integer FK -> steps.id | approval gates this step's next prompt |
| status | text | pending, approved, rejected, auto |
| decided_by | text | user or "auto" |
| decided_at | datetime | |

## Runner Provider Model

All providers implement one interface, for example `run(prompt, working_dir, config) -> RunnerResult`, where `RunnerResult` carries `stdout`, `stderr`, `exit_code`, `started_at`, and `finished_at`. The orchestrator depends only on this interface, so providers are interchangeable and isolated from state-machine logic. Every implementation captures stdout, stderr, exit code, `started_at`, and `finished_at`.

- **ClaudeCodeRunner** — spawns a subprocess invoking the Claude Code CLI with the prompt, captures its output streams, exit code, and timestamps. Implemented first.
- **CodexRunner** — invokes the Codex coding agent through the same interface and captured fields.
- **ShellRunner** — runs a shell command directly for steps that are plain commands rather than agent prompts, capturing the same fields.
- **MockRunner** — returns canned results with no external process; used in tests to exercise the state machine, approval gate, and `maxLoops` deterministically.

## Approval Model

By default, every generated next prompt passes through an approval gate: the run enters `WAITING_APPROVAL`, an `approvals` record is created with status `pending`, and the next prompt does not execute until a user approves it. Approval is the default, and rejection (or a stop request) moves the run to `STOPPED`. Auto-run is optional and must be set explicitly per run via the `auto_run` flag; when enabled, the gate is auto-resolved (`status = auto`) and the run proceeds to `RUNNING_NEXT` without manual input. Regardless of approval mode, the loop is bounded by `maxLoops`, so an auto-run run still stops after the configured number of invocations.

## Safety Model

- **No secret access** — runners and generators do not read, print, or modify secret files (for example `.env`, key files, credentials).
- **No hardcoded secrets** — no credentials or tokens are embedded in code or generated prompts; provider config holds only non-secret settings.
- **No destructive commands by default** — generated prompts and shell steps avoid destructive operations (deleting files, force-pushing, dropping data) unless the user explicitly requested them.
- **Bounded loops** — `maxLoops` caps the number of agent invocations per run, preventing runaway iteration.
- **Provider isolation** — runner logic is confined behind the runner interface, so a misbehaving provider cannot alter orchestrator or persistence behavior, and providers can be swapped without touching the state machine.

## Example User Flow

1. The user runs `autopromptrunner run --project signup-api --prompt "add input validation to the signup endpoint and its tests" --provider claude_code --max-loops 5` without `--auto-run`.
2. A `runs` row is created (`status = CREATED`) referencing the `signup-api` project and the `claude_code` provider, then moves to `QUEUED`.
3. The orchestrator enters `RUNNING`; `ClaudeCodeRunner` spawns the Claude Code CLI with the prompt in the project's `repo_path`.
4. The runner finishes with exit code 0; the run enters `COLLECTING_RESULT`, which writes a `steps` row (stdout, stderr, exit code, `started_at`, `finished_at`) and an `artifacts` row for the modified files.
5. The summarizer records: "Added a validator to the signup endpoint and one unit test; tests not yet run."
6. The run enters `GENERATING_NEXT_PROMPT` and produces: "Run the test suite and fix any failures caused by the new validation."
7. The run enters `WAITING_APPROVAL`; because `auto_run` is false, a `pending` approval is created and execution pauses.
8. The user runs `autopromptrunner approve --run 12`; the approval becomes `approved` and the run enters `RUNNING_NEXT`, increments `loop_count` to 1 (below `maxLoops` = 5), and loops back to `RUNNING` with the new prompt.
9. Subsequent steps repeat. When a step's summary reports all tests passing and the task complete, the run transitions to `DONE`. If the user had instead rejected an approval, it would transition to `STOPPED`; if five invocations elapsed without completion, it would also end in `STOPPED`.

## Example Next-Prompt Generation Flow

Input collected from the prior step:

- exit_code: `1`
- stdout (excerpt): `2 passed, 1 failed — test_signup_rejects_empty_email`
- stderr (excerpt): `AssertionError: expected 400, got 500`
- summary: "Validation added, but the empty-email case returns 500 instead of 400; one test fails."

Generation logic:

1. The generator reads the summary and detects a failing test plus a stderr assertion mismatch.
2. It identifies the specific failing case (`test_signup_rejects_empty_email`) and the observed-vs-expected status codes (500 vs 400).
3. It constructs a targeted next prompt: "The test `test_signup_rejects_empty_email` fails: an empty email returns HTTP 500 instead of 400. Fix the signup endpoint so empty-email input returns a 400 validation error, then re-run the test suite and report results."
4. The next prompt is stored on the step's `next_prompt` field, and the run enters `WAITING_APPROVAL`. Because this step had a nonzero exit but was not treated as fatal (a recoverable test failure), the loop continues; a fatal runner error would instead have routed the run to `FAILED` without generating a next prompt.

## Future Roadmap

- **FastAPI service** — expose the orchestrator over HTTP so runs can be created, approved, and stopped via API.
- **Web UI (React or Next.js)** — a front end over the FastAPI service for starting runs, reviewing summaries and artifacts, and acting on approval gates.
- **Additional runner providers** — complete `CodexRunner` and `ShellRunner`, and add further agent integrations behind the same interface.
- **Parallel runs** — execute multiple runs (and eventually independent steps) concurrently with appropriate scheduling.
- **Persistence and observability improvements** — richer history, structured metrics, tracing of step timing and provider behavior, and a database beyond SQLite for higher concurrency.

## v0.1 Status

### v0.1.0 release status

AutoPromptRunner is at its **v0.1.0 release candidate**: a stable, local-first, end-to-end
tool. The version is a single source of truth in `src/autoprompt_runner/__init__.py` and is
surfaced by `autoprompt-runner version`. See [CHANGELOG.md](CHANGELOG.md) and
[RELEASE_NOTES.md](RELEASE_NOTES.md) for the release summary. The full suite (backend tests,
config validation, mock provider check, and the frontend build) is validated by
`scripts/check_all.sh`.

### Completed capabilities

- CLI-first orchestration with SQLite persistence (projects, runs, steps, approvals,
  artifacts, templates, worktrees, locks, queue, cancellations, provider profiles, recovery).
- Providers: `MockRunner`, `ClaudeCodeRunner`, and `CodexRunner` (subprocess; fail safely
  when the CLI is absent), configured via provider profiles with availability checks.
- Bounded prompt loop, rule-based next-prompt generation, and the approval gate.
- Read-only Git artifact capture and the safety checks (blocked commands, secret-file
  warnings, hard limits).
- Project profiles and reusable prompt templates.
- Git worktree parallel sessions and one-active-lock-per-workspace execution locks.
- A local SQLite-backed run queue with a single background worker, and run cancellation.
- Centralized configuration (TOML file + `AUTOPROMPT_*` environment overrides).
- Search, run comparison, prompt chain history, failure recovery, and JSON export / import.
- A FastAPI HTTP backend and a React/Vite frontend, both covered by end-to-end flow tests.
- Local install / packaging scripts (`setup_local`, `check_all`, `doctor`,
  `package_release`, ...).

### Known limitations

- Local-first only; **no authentication** and no multi-user / hosted deployment.
- No distributed workers (a single local worker); no cloud sync or browser automation.
- No WebSocket / SSE streaming -- run logs update per completed step (polling).
- Claude Code and Codex must be installed and authenticated separately.
- Cancellation of a *running* external agent is best-effort and local to the worker process
  (not guaranteed across machine restarts or from a different process).
- Provider availability checks use command discovery only (no real prompt is executed).
- Export redaction is best-effort, not a secrecy guarantee.

### Post-v0.1 roadmap

- A CI workflow and an automated release-verification pass.
- Authentication and multi-user / hosted deployment.
- Distributed workers and concurrency beyond one local worker.
- True live streaming of run logs (SSE / WebSocket) -- currently polling only.
- Queue retries/backoff, structured logging, metrics, and richer observability.
- A database beyond SQLite for higher concurrency; cloud sync.
