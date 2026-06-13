# AutoPromptRunner Agent Rules

## Project Identity

AutoPromptRunner is a local-first prompt orchestration tool. It drives external coding agents through prompts, captures their output, and decides whether to continue based on the result. All state, logs, and configuration live on the local machine; no remote service is required to run it.

## Product Goal

Run a configured prompt against a target agent, evaluate the result, and optionally generate and run a follow-up prompt under explicit limits. The first target agent is the Claude Code CLI. Future target agents include Codex CLI, ShellRunner, and MockRunner. The tool must support these agents through one shared abstraction rather than agent-specific code paths scattered across the codebase.

## Non-Goals

- Not a hosted or cloud service; it does not depend on remote orchestration.
- Not a general task scheduler or CI system.
- Not a replacement for the underlying agent CLIs; it invokes them, it does not reimplement them.
- Not an autonomous system that runs unbounded; every run is bounded and gated.

## Working Rules

- Prefer small vertical slices over large changes: ship one runnable path end to end before widening scope.
- Make changes that compile and run after each slice; do not leave the tree in a broken intermediate state.
- Read existing code and configuration before modifying it.
- Keep changes scoped to the stated task; surface unrelated issues in the final report instead of acting on them.

## Safety Rules

- Default to an approval gate before executing generated next prompts. The user confirms before any generated follow-up prompt runs.
- Never create infinite loops. Every loop construct must have a bounded, decreasing termination condition.
- Always enforce maxLoops. The loop count is checked before each iteration and the run stops when the limit is reached.
- Do not run destructive commands unless explicitly requested by the user. Treat deletes, force operations, resets, and overwrites as destructive.

## File Modification Rules

- Do not silently modify unrelated files. Touch only files required by the current task.
- List every file written or deleted in the final report.
- Never read, print, or modify secret files (for example `.env`, key files, credential stores, token caches).
- Prefer additive edits and targeted replacements over rewriting whole files.

## Agent Runner Rules

- Keep provider-specific logic isolated behind a common runner interface. Each agent (Claude Code CLI, Codex CLI, ShellRunner, MockRunner) implements the same interface.
- The shared interface accepts a prompt plus run context and returns a structured result; callers must not branch on the concrete agent type.
- Always capture stdout, stderr, exit code, started_at, and finished_at for every agent execution.
- A non-zero exit code is a result to record and report, not a reason to silently retry.

## Prompt Loop Rules

- Each iteration: build prompt, run the agent, record the result, evaluate, then decide whether to continue.
- Always enforce maxLoops; stop when the configured maximum is reached regardless of the agent's output.
- Never create infinite loops; a missing or unmet stop condition must terminate the run, not extend it.
- Before running any generated next prompt, pass through the approval gate by default.

## Provider Adapter Rules

- Each provider adapter translates the common interface into one concrete agent invocation and nothing more.
- Keep provider-specific logic (command construction, argument formatting, output parsing) inside its adapter; do not leak it into the loop or reporting layers.
- Adding a new provider means adding one adapter that satisfies the interface, without editing the loop logic.
- MockRunner exists for tests and dry runs; it must satisfy the same interface as real adapters.

## Logging Rules

- For every agent execution, persist stdout, stderr, exit code, started_at, and finished_at.
- Write logs to local storage; do not transmit them off the machine.
- Never log secret values or the contents of secret files; redact anything sourced from credentials.
- Logs must be sufficient to reconstruct what ran, in what order, and with what outcome.

## Testing Rules

- Use MockRunner to test loop, gating, and reporting logic without invoking real agents.
- Cover the termination paths: maxLoops reached, stop condition met, and non-zero exit code.
- Verify the approval gate blocks execution of generated next prompts until approval is given.
- Keep tests deterministic; do not depend on network access or real CLI installation.

## Git / Diff Rules

- Keep diffs small and scoped to the task, matching the small-vertical-slice rule.
- Do not stage or commit unrelated changes; do not modify files outside the task scope.
- Do not commit secrets or generated logs.
- Commit or push only when the user requests it.

## Token Budget Rules

- Treat each agent invocation as a cost; keep prompts and context to what the task needs.
- maxLoops also bounds total token spend; do not raise it to work around a failing stop condition.
- Trim captured output passed back into follow-up prompts to the relevant portion.
- Prefer the smallest prompt that produces a correct result.

## Forbidden Actions

- Never hardcode secrets.
- Never read, print, or modify secret files.
- Never create infinite loops or bypass maxLoops.
- Never run destructive commands unless explicitly requested by the user.
- Never silently modify unrelated files.
- Never execute a generated next prompt without passing the approval gate (unless the user has explicitly disabled it).

## Required Final Report Format

Final reports must be compact. Emit this template at the end of every run, keeping each field short:

```
## Run Report
- Summary: <one or two sentences on what changed>
- Files touched: <path1, path2, ... or "none">
- Commands run: <command1; command2; ... or "none">
- Results / exit codes: <command -> exit code, agent run -> exit code>
- Next step: <single suggested next step>
```

Include only these fields. Use "none" where a field is empty rather than omitting it.
