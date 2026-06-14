# AutoPromptRunner v0.1.0 Release Notes

## 1. Summary

AutoPromptRunner v0.1.0 is the first **local-first** release candidate: a CLI-first tool
that drives a coding-agent CLI (Claude Code, Codex, or an offline mock) through a bounded,
approval-gated prompt loop, persisting every command, prompt, step, and artifact to a local
SQLite database. It ships a command-line interface, an optional FastAPI HTTP backend, and a
minimal React/Vite web UI. No remote service, account, or network is required to run it; see
[CHANGELOG.md](CHANGELOG.md) for the full capability list and [README.md](README.md) for the
complete feature tour.

## 2. What works

- CLI orchestration with SQLite persistence (projects, runs, steps, approvals, artifacts).
- Providers: `mock` (offline), `claude-code`, and `codex` (subprocess; fail safely when the
  CLI is absent), configurable via provider profiles with availability checks.
- Bounded prompt loop, rule-based next-prompt generation, and a default approval gate.
- Read-only Git artifact capture and deterministic safety checks.
- Project profiles, prompt templates, Git worktree sessions, and workspace locks.
- Local queue + background worker, and best-effort run cancellation.
- Config file + `AUTOPROMPT_*` environment overrides.
- Search, run comparison, prompt chain history, failure recovery, and JSON export / import.
- A FastAPI backend and a React/Vite frontend, plus local install / packaging scripts.

## 3. Local setup

```
git clone https://github.com/opop20207/AutoPromptRunner.git
cd AutoPromptRunner
scripts/setup_local.sh          # venv + backend + frontend deps + config + seed
```

Or install manually: `pip install -e ".[dev]"` and `( cd frontend && npm install )`. Verify
the environment with `scripts/doctor.sh` and the whole project with `scripts/check_all.sh`.

## 4. Basic workflow

```
# (in three terminals, after setup)
scripts/dev_api.sh              # HTTP API  -> http://127.0.0.1:8000
scripts/dev_worker.sh           # queue worker
scripts/dev_frontend.sh         # web UI    -> http://localhost:5173

# or via the CLI only:
python -m autoprompt_runner.cli project add --name demo --repo-path . --provider mock
python -m autoprompt_runner.cli run --project demo --prompt "Review this project" --max-loops 3
python -m autoprompt_runner.cli approve-next --run-id 1     # or: reject-next --run-id 1
python -m autoprompt_runner.cli show-artifacts --run-id 1
```

## 5. Provider setup

- `mock` works out of the box (offline, deterministic) and is always available.
- `claude-code` requires the **Claude Code CLI** installed and authenticated; `codex`
  requires the **Codex CLI**. AutoPromptRunner never installs them and never handles their
  API keys.
- Seed and inspect provider profiles: `provider seed`, `provider list`,
  `provider check --name claude-code`. Availability is checked by command discovery only —
  no real prompt is executed.

## 6. Safety model

- Default **approval gate** before any generated next prompt runs, and a hard `max_loops`
  bound so the loop can never run unbounded.
- **Blocked-command scan** of the prompt before execution (destructive patterns), a
  **secret-file denylist** (name-only; contents are never read), and **large-diff** warnings;
  a risky change forces an approval gate even in auto-run mode.
- Git capture is strictly **read-only**; the tool never stages, commits, resets, or cleans.
- Export **redacts** secret-like artifact content by default (best-effort).

## 7. Known limitations

- Local-first only.
- No authentication.
- No multi-user deployment.
- No distributed workers.
- No WebSocket / SSE streaming.
- Polling only for logs.
- No cloud sync.
- No browser automation.
- Claude Code and Codex must be installed separately.
- Running-process cancellation is best-effort.
- Provider availability checks do not execute real prompts.
- Export redaction is best-effort.

## 8. Recommended next steps

- Try the `mock` provider end to end first (no external CLI needed), then point a project at
  a real repository with `claude-code` or `codex`.
- Run `scripts/check_all.sh` before relying on a local build, and `scripts/doctor.sh` to
  diagnose a new machine.
- Post-v0.1 candidates: a CI workflow, optional auth for shared use, true log streaming
  (SSE/WebSocket), retries/backoff for the queue, and richer metrics — see
  [PROJECT.md](PROJECT.md) for the roadmap.
