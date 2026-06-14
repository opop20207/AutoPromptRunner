"""Rule-based next-prompt generation.

``PromptGenerator`` turns a completed step's context into the next prompt using a small
set of explicit, deterministic rules keyed on the execution outcome (success/failure),
whether files changed, how many changed, whether the output looks like a test failure,
and how close the run is to ``max_loops``. It calls no external AI APIs, uses no
network access, and invents no file changes, paths, or test results -- it only restates
the task and forwards the relevant prior signal so the next step can proceed safely.
"""

from __future__ import annotations

from ..models import NextPrompt, PromptGenerationContext

# Substrings (lower-cased) that signal a likely test/failure in runner output. Kept
# specific so benign output -- including echoed prompts that mention "tests" -- does not
# trip the detector.
_TEST_FAILURE_INDICATORS = (
    "traceback",
    "assertionerror",
    "assertion error",
    "pytest",
    "unittest",
    "failed",
    "failure",
)

# A changed-file count above this is treated as a broad change worth reviewing.
_MANY_FILES_THRESHOLD = 5


def _short(text: str, limit: int) -> str:
    """Collapse whitespace and truncate ``text`` to ``limit`` characters."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)] + "..."


def _looks_like_test_failure(stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    return any(indicator in text for indicator in _TEST_FAILURE_INDICATORS)


# Previews of stored runner output included in a recovery prompt (kept short and never the
# full captured output, so a huge log is not echoed back).
_RECOVERY_STDERR_PREVIEW = 200
_RECOVERY_STDOUT_PREVIEW = 160
_RECOVERY_FILES_PREVIEW = 120


def build_recovery_prompt(
    root_prompt: str,
    failed_prompt: str,
    stderr: str,
    stdout: str,
    exit_code,
    changed_files=None,
    diff_stat: str = "",
) -> str:
    """Build a focused, deterministic recovery prompt for a failed step.

    Rule-based (no AI, no network): it restates the original task, forwards a short preview
    of the *stored* stderr (primary) or stdout (secondary), and instructs the agent to fix
    only the failure, preserve intended behavior, rerun the relevant tests/command, and
    report blockers. It invents no file paths or test names -- it only echoes stored signal.
    """
    root = _short(root_prompt, 80)
    parts = [
        "Recover from the failed AutoPromptRunner step. Fix only the failure from the previous step.",
    ]
    if (stderr or "").strip():
        parts.append(f"Use stderr as the primary source: {_short(stderr, _RECOVERY_STDERR_PREVIEW)}.")
    elif (stdout or "").strip():
        parts.append(
            f"There was no stderr; use the stdout output as context: {_short(stdout, _RECOVERY_STDOUT_PREVIEW)}."
        )
    else:
        parts.append(f"The step exited with code {exit_code} and produced no output; inspect the workspace state.")
    changed = [f for f in (changed_files or []) if f]
    if changed:
        parts.append(f"Files changed in the failed step: {_short(', '.join(changed[:5]), _RECOVERY_FILES_PREVIEW)}.")
    parts.append("Do not expand scope or start unrelated refactors; preserve the intended behavior.")
    parts.append(
        "Rerun the relevant tests or the failed command, and report changed files, test result, "
        "and remaining blockers."
    )
    parts.append(f'Original task: "{root}".')
    return " ".join(parts)


class PromptGenerator:
    """Generate a compact, actionable, deterministic next prompt from step context."""

    def generate(self, context: PromptGenerationContext) -> NextPrompt:
        """Return the next prompt for the step after ``context.loop_index``.

        Output is deterministic: identical context always produces identical text.
        """
        root = _short(context.root_prompt, 80)
        next_index = context.loop_index + 1
        if context.exit_code != 0:
            return self._failure_prompt(context, root, next_index)
        return self._success_prompt(context, root, next_index)

    # -- failure outcomes -----------------------------------------------------

    def _failure_prompt(self, ctx: PromptGenerationContext, root: str, next_index: int) -> NextPrompt:
        if _looks_like_test_failure(ctx.stdout, ctx.stderr):
            prompt = (
                "Fix the failing tests from the previous step first. Use the test output as the primary "
                "source and preserve the intended behavior. Re-run the relevant test target and report "
                f'remaining failures. Do not expand scope. Task: "{root}".'
            )
            return NextPrompt(prompt=prompt, kind="fix_tests", loop_index=next_index)

        if ctx.stderr.strip():
            prompt = (
                "Fix the failure from the previous step. Use stderr as the primary source: "
                f"{_short(ctx.stderr, 160)}. Do not expand scope. Re-run the relevant command or tests and "
                f'report remaining blockers. Task: "{root}".'
            )
            return NextPrompt(prompt=prompt, kind="fix", loop_index=next_index)

        prompt = (
            f"Diagnose the failed previous step (exit code {ctx.exit_code}). There was no stderr; check the "
            "stdout output and the workspace state, then make a minimal fix. Do not expand scope. "
            f'Task: "{root}". Previous output: {_short(ctx.stdout, 120)}'
        )
        return NextPrompt(prompt=prompt, kind="diagnose", loop_index=next_index)

    # -- success outcomes -----------------------------------------------------

    def _success_prompt(self, ctx: PromptGenerationContext, root: str, next_index: int) -> NextPrompt:
        changed = [f for f in (ctx.changed_files or []) if f]
        count = len(changed)

        if ctx.loop_index + 1 >= ctx.max_loops:
            prompt = (
                "This is the final allowed loop. Summarize the work completed so far and list any remaining "
                f'tasks as concrete next steps. Do not start large new work. Task: "{root}".'
            )
            return NextPrompt(prompt=prompt, kind="wrapup", loop_index=next_index)

        if _looks_like_test_failure(ctx.stdout, ctx.stderr):
            prompt = (
                "The previous output indicates failing tests. Fix the failing tests first while preserving the "
                "intended behavior, then re-run the relevant test target and report results. Do not expand "
                f'scope. Task: "{root}".'
            )
            return NextPrompt(prompt=prompt, kind="fix_tests", loop_index=next_index)

        if count > _MANY_FILES_THRESHOLD:
            prompt = (
                f"The previous step changed many files ({count}). Review the broad changes, reduce scope if the "
                "change grew too large, and check for accidental or unrelated modifications before continuing. "
                f'Task: "{root}". Diff stat: {_short(ctx.git_diff_stat, 100)}'
            )
            return NextPrompt(prompt=prompt, kind="review_broad", loop_index=next_index)

        if count > 0:
            listed = _short(", ".join(changed[:5]), 100)
            prompt = (
                f"Review the changed files from the previous step: {listed}. Run or improve the relevant tests, "
                "then continue with the next smallest task. Do not expand scope beyond the original task. "
                f'Task: "{root}".'
            )
            return NextPrompt(prompt=prompt, kind="continue", loop_index=next_index)

        prompt = (
            "The previous step reported success but changed no files. Determine whether the task is already "
            "complete; if it is, say so explicitly. If not, make the next smallest concrete change only. Do "
            f'not expand scope. Task: "{root}".'
        )
        return NextPrompt(prompt=prompt, kind="no_changes", loop_index=next_index)
