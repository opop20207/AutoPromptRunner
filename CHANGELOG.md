# Changelog

All notable changes to AutoPromptRunner are recorded here. AutoPromptRunner is a
local-first, CLI-first prompt orchestration tool; see [RELEASE_NOTES.md](RELEASE_NOTES.md)
for the v0.1.0 release summary and [README.md](README.md) for the full feature tour.

The version is a single source of truth in `src/autoprompt_runner/__init__.py`
(`__version__`), surfaced by `autoprompt-runner version` and the `pyproject.toml` metadata.

## 0.1.0

First release candidate: a stable, local-first, end-to-end tool. No remote service is
required to run it.

### Added

- **CLI** — full command surface (`init-db`, `project`, `template`, `provider`, `worktree`,
  `run`, `approve-next` / `reject-next`, `list-runs` / `show-run` / `show-artifacts` /
  `show-artifact`, `locks`, `queue`, `worker`, `config`, `search`, `compare`, `chain`,
  `recovery`, `export` / `import`, `safety-check`). Console entry point
  `autoprompt-runner = autoprompt_runner.cli:main`.
- **FastAPI backend** (optional `api` extra) exposing the same operations over HTTP — no
  auth, no WebSocket/SSE.
- **React + Vite + TypeScript frontend** — a thin local-first dashboard over the API (no
  router/state/UI framework).
- **SQLite persistence** of projects, settings, runs, steps, approvals, artifacts,
  templates, worktrees, locks, queue, cancellations, provider profiles, and recovery
  attempts.
- **Providers** — `mock` (offline, deterministic), `claude-code`, and `codex` (subprocess
  adapters that fail safely when the CLI is absent).
- **Project profiles** and **provider profiles** (configurable command / timeout / args +
  availability checks) and reusable **prompt templates**.
- **Prompt loop** with a bounded `max_loops` and a default **approval gate**.
- **Git artifact capture** (read-only status / diff / diff-stat / changed files) and
  **safety checks** (blocked-command scan, secret-file warnings, hard limits).
- **Git worktree** parallel sessions and one-active-lock-per-workspace **workspace locks**.
- **Local SQLite-backed queue** with a single background **worker**, and run
  **cancellation** (best-effort for a running process).
- **Config file + environment overrides** (`config show` / `validate` / `init`).
- **Search** across runs, logs, prompts, and artifacts (SQLite `LIKE`).
- **Run comparison** (metadata, steps, changed files, diff stats, artifact counts).
- **Prompt chain history** view (root → step prompts → next prompts, per run).
- **Failure recovery** (rule-based recovery prompt + a new linked recovery run).
- **Export / import** of data as portable JSON (best-effort redaction, non-destructive
  import).
- **Local packaging / install scripts** (`setup_local`, `install_backend`,
  `install_frontend`, `build_frontend`, `dev_api`, `dev_worker`, `dev_frontend`,
  `check_all`, `doctor`, `package_release`).

### Changed

- First tracked release — this entry is the baseline for future changelogs.

### Fixed

- Release-readiness pass: verified the CLI entry point and version, `pyproject.toml`
  metadata, the helper scripts, the frontend build, and the full backend test suite; no
  release-blocking issues were found.

### Known limitations

- Local-first only; **no authentication** and no multi-user / hosted deployment.
- No distributed workers (a single local worker), and no cloud sync or browser automation.
- No WebSocket / SSE streaming — run logs use **polling** only.
- Claude Code and Codex must be installed and authenticated **separately**; AutoPromptRunner
  never installs them or handles their keys.
- Cancellation of a *running* external agent is **best-effort** and local to the worker
  process (not guaranteed across machine restarts or from another process).
- Provider availability checks use **command discovery only** — they never execute a real
  prompt.
- Export redaction is **best-effort**, not a secrecy guarantee (run prompts and step
  stdout/stderr are exported).
