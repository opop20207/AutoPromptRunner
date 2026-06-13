"""Prompt template management for AutoPromptRunner.

A prompt template stores reusable prompt text that may contain ``{{placeholder}}``
tokens. Rendering is a plain, deterministic string substitution:

* Known placeholders are replaced with the supplied value (or an empty string when the
  value is missing).
* Unknown placeholders are left unchanged.
* Nothing is executed and no expression is ever evaluated -- the value is inserted
  literally. There is no network access.

The persistence CRUD lives in :mod:`autoprompt_runner.storage`; it is re-exported here
so callers can use ``templates.create_template`` / ``templates.list_templates`` / ... as
the single entry point, alongside :func:`render_template` and :func:`seed_templates`.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional

from .models import Template
from .storage import (  # noqa: F401  (re-exported as the template CRUD surface)
    create_template,
    delete_template,
    get_template_by_id,
    get_template_by_name,
    list_templates,
    update_template,
)

# The placeholders a template body may reference. Anything else is left untouched.
SUPPORTED_PLACEHOLDERS = (
    "project_name",
    "workspace",
    "goal",
    "changed_files",
    "last_error",
    "extra_context",
)


def _join_changed_files(value) -> str:
    """Normalize the ``changed_files`` value (list or string) to a compact string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return ", ".join(str(item) for item in value if str(item).strip())


def build_render_values(
    *,
    project_name: Optional[str] = None,
    workspace: Optional[str] = None,
    goal: Optional[str] = None,
    changed_files=None,
    last_error: Optional[str] = None,
    extra_context: Optional[str] = None,
) -> Dict[str, str]:
    """Build the placeholder->value map from typical run inputs (missing -> "")."""
    return {
        "project_name": project_name or "",
        "workspace": workspace or "",
        "goal": goal or "",
        "changed_files": _join_changed_files(changed_files),
        "last_error": last_error or "",
        "extra_context": extra_context or "",
    }


def render_template(body: str, values: Optional[Mapping[str, Optional[str]]] = None) -> str:
    """Render ``body`` by substituting known ``{{placeholder}}`` tokens.

    Only the placeholders in :data:`SUPPORTED_PLACEHOLDERS` are replaced. A missing value
    renders as an empty string; unknown placeholders are left exactly as written. The
    substitution is literal -- no code is executed and no expression is evaluated.
    """
    values = values or {}
    rendered = body or ""
    for key in SUPPORTED_PLACEHOLDERS:
        token = "{{" + key + "}}"
        if token in rendered:
            replacement = values.get(key)
            rendered = rendered.replace(token, "" if replacement is None else str(replacement))
    return rendered


# Built-in templates seeded by ``template seed`` / ``POST /templates/seed``. Each is a
# compact, scope-conscious prompt aligned with the next-prompt generation rules.
DEFAULT_TEMPLATES = (
    {
        "name": "Continue next task",
        "description": "Continue work with the next smallest concrete task.",
        "tags": ["workflow", "continue"],
        "body": (
            "Continue work on {{project_name}}. Goal: {{goal}}. "
            "Do the next smallest concrete task, keep the change scoped, and run the tests. "
            "{{extra_context}}"
        ),
    },
    {
        "name": "Fix failing tests",
        "description": "Fix the current failing tests, smallest cause first.",
        "tags": ["tests", "fix"],
        "body": (
            "Fix the failing tests in {{project_name}}. Goal: {{goal}}. "
            "Use the test output as the source of truth, fix the smallest cause first, "
            "preserve intended behavior, and re-run the tests. Last error: {{last_error}}"
        ),
    },
    {
        "name": "Review git diff",
        "description": "Review the current diff for scope creep and obvious bugs.",
        "tags": ["review", "git"],
        "body": (
            "Review the current git diff for {{project_name}} in {{workspace}}. "
            "Changed files: {{changed_files}}. Check for accidental changes, scope creep, "
            "and obvious bugs, then summarize the findings. {{extra_context}}"
        ),
    },
    {
        "name": "Refactor small module",
        "description": "Refactor one small module without changing behavior.",
        "tags": ["refactor"],
        "body": (
            "Refactor one small module in {{project_name}} for clarity without changing behavior. "
            "Goal: {{goal}}. Keep the change small, run the tests, and do not expand scope. "
            "{{extra_context}}"
        ),
    },
    {
        "name": "Update documentation",
        "description": "Update docs to match current behavior, factual and compact.",
        "tags": ["docs"],
        "body": (
            "Update the documentation for {{project_name}} to match the current behavior. "
            "Goal: {{goal}}. Keep edits factual and compact and do not invent features. "
            "{{extra_context}}"
        ),
    },
    {
        "name": "Generate next prompt only",
        "description": "Propose only the next prompt; make no code changes.",
        "tags": ["prompt", "meta"],
        "body": (
            "Based on the latest result for {{project_name}}, propose only the next prompt to run. "
            "Goal: {{goal}}. Do not make code changes; output a single compact, actionable prompt."
        ),
    },
    {
        "name": "Diagnose failure",
        "description": "Find the most likely root cause and the smallest fix.",
        "tags": ["diagnose", "debug"],
        "body": (
            "Diagnose the failure in {{project_name}}. Last error: {{last_error}}. "
            "Identify the most likely root cause from the workspace state and the error, "
            "then propose the smallest fix. Do not apply large changes yet. {{extra_context}}"
        ),
    },
    {
        "name": "Reduce scope after large diff",
        "description": "Trim an over-large change back to the smallest needed edit.",
        "tags": ["scope", "cleanup"],
        "body": (
            "The recent change to {{project_name}} is too large. Changed files: {{changed_files}}. "
            "Reduce scope: revert unrelated edits, keep only the smallest change that meets the "
            "goal ({{goal}}), and re-run the tests."
        ),
    },
)


def seed_templates(db_path: str, overwrite: bool = False) -> Dict[str, int]:
    """Insert the built-in templates that are missing.

    Existing templates are left untouched (a user may have modified one) unless
    ``overwrite`` is explicitly true, in which case their description/body/tags are
    refreshed from the built-in definition. Returns a summary count.
    """
    seeded = 0
    skipped = 0
    for spec in DEFAULT_TEMPLATES:
        existing = get_template_by_name(db_path, spec["name"])
        if existing is None:
            create_template(
                db_path,
                name=spec["name"],
                body=spec["body"],
                description=spec["description"],
                tags=list(spec["tags"]),
            )
            seeded += 1
        elif overwrite:
            update_template(
                db_path,
                existing.id,
                description=spec["description"],
                body=spec["body"],
                tags=list(spec["tags"]),
            )
            seeded += 1
        else:
            skipped += 1
    return {"seeded": seeded, "skipped": skipped, "total": len(DEFAULT_TEMPLATES)}


def default_template_names() -> List[str]:
    """Return the names of the built-in templates."""
    return [spec["name"] for spec in DEFAULT_TEMPLATES]
