"""Deterministic next-prompt generation.

``PromptGenerator`` turns a completed step's result into the next prompt using simple,
fixed templates. It calls no external AI APIs, uses no network access, and invents no
file changes -- it only restates the task and forwards the relevant prior output so the
next step can continue or fix the failure safely.
"""

from __future__ import annotations

from ..models import NextPrompt


def _short(text: str, limit: int) -> str:
    """Collapse whitespace and truncate ``text`` to ``limit`` characters."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)] + "..."


class PromptGenerator:
    """Generate a compact, deterministic next prompt from a step result."""

    def generate(
        self,
        root_prompt: str,
        previous_prompt: str,
        stdout: str,
        stderr: str,
        exit_code: int,
        loop_index: int,
    ) -> NextPrompt:
        """Return the next prompt for the step after ``loop_index``.

        ``exit_code == 0`` yields a "continue" prompt focused on verifying and
        advancing; a non-zero ``exit_code`` yields a "fix" prompt focused on the
        failure. Output is deterministic: the same inputs always produce the same text.
        """
        root_short = _short(root_prompt, 80)
        next_index = loop_index + 1

        if exit_code == 0:
            prompt = (
                "Continue from the previous completed step. "
                "Review the changes, identify the next smallest implementation task, "
                "apply it, run relevant tests, and report changed files. "
                "Do not expand scope beyond the original task. "
                f'Original task: "{root_short}". '
                f"(step {loop_index} succeeded, exit 0; output: {_short(stdout, 120)})"
            )
            return NextPrompt(prompt=prompt, kind="continue", loop_index=next_index)

        prompt = (
            "Fix the failure from the previous step. "
            "Use stderr and failing output as the primary source. Do not expand scope. "
            "Run relevant tests again and report remaining failures. "
            f'Original task: "{root_short}". '
            f"(step {loop_index} failed, exit {exit_code}; stderr: {_short(stderr, 160)})"
        )
        return NextPrompt(prompt=prompt, kind="fix", loop_index=next_index)
