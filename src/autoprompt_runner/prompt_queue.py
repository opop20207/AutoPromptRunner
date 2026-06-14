"""Claude Code app prompt queue: register prompts and inject them one at a time.

A prompt queue is bound to an :class:`~autoprompt_runner.models.AppTarget` and holds an ordered
list of prompts (e.g. Prompt#34, Prompt#35, ...). AutoPromptRunner injects the **current** prompt
into the Claude Code app (via :mod:`app_injection`), the prompt then waits for the user to mark it
complete, and only then can the next prompt be injected. The queue **never runs the Claude Code
CLI** -- it drives the desktop app.

Execution rules enforced here:

* Injection is always an explicit action; nothing is injected automatically.
* Only one prompt per queue may be active (INJECTING / SUBMITTED / WAITING_COMPLETION) at a time.
* After injection the prompt becomes WAITING_COMPLETION; the user must mark it complete before the
  next prompt becomes READY_TO_INJECT.
* A paused queue blocks injection; cancelling marks all non-terminal prompts CANCELLED.
* Only PENDING prompts may be reordered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from . import app_injection, app_targets, storage
from .models import AppTarget, PromptQueue, QueuedPrompt

# Prompt-queue statuses (mirror storage).
QUEUE_DRAFT = storage.PROMPT_QUEUE_DRAFT
QUEUE_READY = storage.PROMPT_QUEUE_READY
QUEUE_RUNNING = storage.PROMPT_QUEUE_RUNNING
QUEUE_PAUSED = storage.PROMPT_QUEUE_PAUSED
QUEUE_DONE = storage.PROMPT_QUEUE_DONE
QUEUE_FAILED = storage.PROMPT_QUEUE_FAILED
QUEUE_CANCELLED = storage.PROMPT_QUEUE_CANCELLED
_QUEUE_TERMINAL = (QUEUE_DONE, QUEUE_FAILED, QUEUE_CANCELLED)

# Queued-prompt statuses (mirror storage).
PENDING = storage.QUEUED_PROMPT_PENDING
READY_TO_INJECT = storage.QUEUED_PROMPT_READY_TO_INJECT
INJECTING = storage.QUEUED_PROMPT_INJECTING
SUBMITTED = storage.QUEUED_PROMPT_SUBMITTED
WAITING_COMPLETION = storage.QUEUED_PROMPT_WAITING_COMPLETION
DONE = storage.QUEUED_PROMPT_DONE
FAILED = storage.QUEUED_PROMPT_FAILED
SKIPPED = storage.QUEUED_PROMPT_SKIPPED
CANCELLED = storage.QUEUED_PROMPT_CANCELLED
_PROMPT_TERMINAL = (DONE, FAILED, SKIPPED, CANCELLED)
# A prompt is "active" (occupies the single injection slot) in any of these states.
_PROMPT_ACTIVE = (INJECTING, SUBMITTED, WAITING_COMPLETION)
_PROMPT_INJECTABLE = (PENDING, READY_TO_INJECT)

# Compact preview length for summaries / CLI output.
PREVIEW_CHARS = 120

Injector = Callable[..., app_injection.InjectionResult]


class PromptQueueError(Exception):
    """Raised for queue problems.

    ``kind`` is ``"not_found"`` (queue/prompt/target missing), ``"invalid"`` (bad input), or
    ``"invalid_state"`` (operation not allowed in the current state). Callers map it to a CLI
    exit code or an HTTP status (404 / 400 / 409).
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class QueueSummary:
    queue: PromptQueue
    target: Optional[AppTarget]
    prompts: List[QueuedPrompt]
    current: Optional[QueuedPrompt]
    waiting: Optional[QueuedPrompt]
    counts: Dict[str, int]


@dataclass
class InjectOutcome:
    summary: QueueSummary
    prompt: QueuedPrompt
    injection: app_injection.InjectionResult
    target_summary: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def preview(text: Optional[str], limit: int = PREVIEW_CHARS) -> str:
    """Return a compact single-line preview of a prompt (no secrets are added)."""
    norm = " ".join((text or "").split())
    return norm if len(norm) <= limit else norm[:limit] + "…"


# -- queue lifecycle ---------------------------------------------------------


def _require_queue(db_path: str, queue_id: int) -> PromptQueue:
    queue = storage.get_prompt_queue(db_path, queue_id)
    if queue is None:
        raise PromptQueueError("not_found", f"prompt queue {queue_id} not found")
    return queue


def create_queue(
    db_path: str,
    *,
    name: str,
    app_target_id: Optional[int] = None,
    description: Optional[str] = None,
    project_path: Optional[str] = None,
) -> PromptQueue:
    """Create a prompt queue (DRAFT), optionally bound to an app target."""
    db_path = storage.init_db(db_path)
    clean = (name or "").strip()
    if not clean:
        raise PromptQueueError("invalid", "queue name must not be empty")
    target: Optional[AppTarget] = None
    if app_target_id is not None:
        target = storage.get_app_target(db_path, app_target_id)
        if target is None:
            raise PromptQueueError("not_found", f"app target {app_target_id} not found")
    if not project_path and target is not None:
        project_path = target.project_path
    queue_id = storage.create_prompt_queue(
        db_path, name=clean, description=description, app_target_id=app_target_id,
        project_path=project_path, status=QUEUE_DRAFT,
    )
    return storage.get_prompt_queue(db_path, queue_id)


def create_queue_for_target_name(db_path: str, *, name: str, target_name: Optional[str] = None, **kwargs) -> PromptQueue:
    """Create a queue, resolving an app target by name (for the CLI). Raises if the target is missing."""
    db_path = storage.init_db(db_path)
    app_target_id = None
    if target_name:
        target = storage.get_app_target_by_name(db_path, target_name)
        if target is None:
            raise PromptQueueError("not_found", f"app target '{target_name}' not found")
        app_target_id = target.id
    return create_queue(db_path, name=name, app_target_id=app_target_id, **kwargs)


def list_queues(db_path: str) -> List[PromptQueue]:
    """Return all prompt queues (newest first)."""
    return storage.list_prompt_queues(db_path)


def delete_queue(db_path: str, queue_id: int) -> None:
    """Delete a prompt queue and its prompts."""
    _require_queue(db_path, queue_id)
    storage.delete_prompt_queue(db_path, queue_id)


def add_prompt_to_queue(
    db_path: str, queue_id: int, *, prompt: str, title: Optional[str] = None, position: Optional[int] = None
) -> QueuedPrompt:
    """Append a prompt to a queue (status PENDING). Never auto-injects."""
    queue = _require_queue(db_path, queue_id)
    if queue.status in _QUEUE_TERMINAL:
        raise PromptQueueError("invalid_state", f"queue is {queue.status}; cannot add prompts")
    if not (prompt or "").strip():
        raise PromptQueueError("invalid", "prompt text must not be empty")
    prompt_id = storage.add_queued_prompt(db_path, queue_id=queue_id, prompt=prompt, title=title, position=position)
    if queue.status == QUEUE_DRAFT:
        storage.update_prompt_queue_status(db_path, queue_id, QUEUE_READY)
    return storage.get_queued_prompt(db_path, prompt_id)


def reorder_prompt(db_path: str, prompt_id: int, new_position: int) -> QueuedPrompt:
    """Move a PENDING prompt to a new position. Non-PENDING prompts cannot be reordered."""
    target = storage.get_queued_prompt(db_path, prompt_id)
    if target is None:
        raise PromptQueueError("not_found", f"queued prompt {prompt_id} not found")
    if target.status != PENDING:
        raise PromptQueueError("invalid_state", f"only PENDING prompts can be reordered (this is {target.status})")
    storage.reorder_queued_prompt(db_path, prompt_id, new_position)
    return storage.get_queued_prompt(db_path, prompt_id)


# -- injection / completion --------------------------------------------------


def _active_prompt(db_path: str, queue_id: int) -> Optional[QueuedPrompt]:
    """Return the prompt occupying the single injection slot (INJECTING/SUBMITTED/WAITING), if any."""
    for prompt in storage.list_queued_prompts(db_path, queue_id):
        if prompt.status in _PROMPT_ACTIVE:
            return prompt
    return None


def inject_current_prompt(
    db_path: str,
    queue_id: int,
    *,
    injector: Optional[Injector] = None,
    restore_clipboard_after: bool = False,
) -> InjectOutcome:
    """Inject the current prompt into the queue's app target. Explicit action only.

    Rejects when the queue is paused/terminal, when another prompt is already waiting for
    completion, when there is no prompt to inject, or when the target is missing/disabled. On
    success the prompt becomes WAITING_COMPLETION and the user must mark it complete next.
    """
    db_path = storage.init_db(db_path)
    queue = _require_queue(db_path, queue_id)
    if queue.status == QUEUE_PAUSED:
        raise PromptQueueError("invalid_state", "queue is paused; resume it before injecting")
    if queue.status in _QUEUE_TERMINAL:
        raise PromptQueueError("invalid_state", f"queue is {queue.status}; cannot inject")
    if queue.app_target_id is None:
        raise PromptQueueError("invalid", "queue has no app target bound; bind one before injecting")
    target = storage.get_app_target(db_path, queue.app_target_id)
    if target is None:
        raise PromptQueueError("not_found", f"app target {queue.app_target_id} not found")
    if target.status == app_targets.STATUS_DISABLED:
        raise PromptQueueError("invalid_state", f"app target '{target.name}' is disabled")

    active = _active_prompt(db_path, queue_id)
    if active is not None:
        raise PromptQueueError(
            "invalid_state",
            f"prompt #{active.id} is {active.status}; mark it complete (or skip it) before injecting the next",
        )
    current = storage.get_current_prompt(db_path, queue_id)
    if current is None or current.status not in _PROMPT_INJECTABLE:
        raise PromptQueueError("invalid_state", "no prompt is ready to inject")

    # Validate (raises InjectionError on empty/disabled), then perform the injection.
    app_injection.validate_injection_request(target, current.prompt)
    if queue.status in (QUEUE_DRAFT, QUEUE_READY):
        storage.update_prompt_queue_status(db_path, queue_id, QUEUE_RUNNING, started_at=_now())
    storage.mark_prompt_injected(db_path, current.id)
    run_injection = injector or app_injection.inject_prompt_to_active_window
    try:
        result = run_injection(
            current.prompt, submit_mode=target.submit_mode, restore_clipboard_after=restore_clipboard_after
        )
    except app_injection.InjectionError as exc:
        # Revert so the user can retry after fixing the cause; record the error.
        storage.update_queued_prompt_status(db_path, current.id, READY_TO_INJECT, last_error=str(exc))
        raise
    storage.mark_prompt_submitted(db_path, current.id)  # -> WAITING_COMPLETION
    storage.mark_app_target_used(db_path, target.id)

    summary = build_queue_summary(db_path, queue_id)
    return InjectOutcome(
        summary=summary,
        prompt=storage.get_queued_prompt(db_path, current.id),
        injection=result,
        target_summary=app_targets.target_summary(target),
    )


def mark_current_complete(db_path: str, queue_id: int) -> QueueSummary:
    """Mark the WAITING_COMPLETION prompt DONE and set the next prompt READY_TO_INJECT."""
    _require_queue(db_path, queue_id)
    current = storage.get_current_prompt(db_path, queue_id)
    if current is None or current.status != WAITING_COMPLETION:
        raise PromptQueueError("invalid_state", "no prompt is awaiting completion")
    storage.mark_prompt_complete(db_path, current.id)
    _advance_queue(db_path, queue_id)
    return build_queue_summary(db_path, queue_id)


def skip_current_prompt(db_path: str, queue_id: int) -> QueueSummary:
    """Mark the current prompt SKIPPED and make the next prompt READY_TO_INJECT."""
    _require_queue(db_path, queue_id)
    current = storage.get_current_prompt(db_path, queue_id)
    if current is None:
        raise PromptQueueError("invalid_state", "no current prompt to skip")
    storage.skip_queued_prompt(db_path, current.id)
    _advance_queue(db_path, queue_id)
    return build_queue_summary(db_path, queue_id)


def _advance_queue(db_path: str, queue_id: int) -> None:
    """After a prompt finishes, ready the next PENDING prompt, or finish the queue when empty."""
    nxt = storage.get_next_pending_prompt(db_path, queue_id)
    if nxt is not None:
        storage.update_queued_prompt_status(db_path, nxt.id, READY_TO_INJECT)
        return
    # No more PENDING prompts: if nothing is still active, the queue is done.
    if storage.get_current_prompt(db_path, queue_id) is None:
        queue = storage.get_prompt_queue(db_path, queue_id)
        if queue is not None and queue.status not in _QUEUE_TERMINAL:
            storage.update_prompt_queue_status(db_path, queue_id, QUEUE_DONE, finished_at=_now())


def pause_queue(db_path: str, queue_id: int) -> QueueSummary:
    """Pause a queue so injection is blocked until resumed."""
    queue = _require_queue(db_path, queue_id)
    if queue.status in _QUEUE_TERMINAL:
        raise PromptQueueError("invalid_state", f"queue is {queue.status}; cannot pause")
    storage.update_prompt_queue_status(db_path, queue_id, QUEUE_PAUSED, paused_at=_now())
    return build_queue_summary(db_path, queue_id)


def resume_queue(db_path: str, queue_id: int) -> QueueSummary:
    """Resume a paused queue (back to RUNNING if started, else READY)."""
    queue = _require_queue(db_path, queue_id)
    if queue.status != QUEUE_PAUSED:
        raise PromptQueueError("invalid_state", f"queue is {queue.status}; only a PAUSED queue can resume")
    resumed = QUEUE_RUNNING if queue.started_at else QUEUE_READY
    storage.update_prompt_queue_status(db_path, queue_id, resumed)
    return build_queue_summary(db_path, queue_id)


def cancel_queue(db_path: str, queue_id: int) -> QueueSummary:
    """Cancel a queue: all non-terminal prompts become CANCELLED and the queue is CANCELLED."""
    queue = _require_queue(db_path, queue_id)
    if queue.status in _QUEUE_TERMINAL:
        raise PromptQueueError("invalid_state", f"queue is already {queue.status}")
    for prompt in storage.list_queued_prompts(db_path, queue_id):
        if prompt.status not in _PROMPT_TERMINAL:
            storage.cancel_queued_prompt(db_path, prompt.id)
    storage.update_prompt_queue_status(db_path, queue_id, QUEUE_CANCELLED, finished_at=_now())
    return build_queue_summary(db_path, queue_id)


# -- summary -----------------------------------------------------------------


def build_queue_summary(db_path: str, queue_id: int) -> QueueSummary:
    """Assemble a read-only snapshot of a queue: target, prompts, current/waiting, and counts."""
    queue = _require_queue(db_path, queue_id)
    target = storage.get_app_target(db_path, queue.app_target_id) if queue.app_target_id else None
    prompts = storage.list_queued_prompts(db_path, queue_id)
    current = storage.get_current_prompt(db_path, queue_id)
    waiting = next((p for p in prompts if p.status == WAITING_COMPLETION), None)
    counts: Dict[str, int] = {}
    for prompt in prompts:
        counts[prompt.status] = counts.get(prompt.status, 0) + 1
    counts["total"] = len(prompts)
    return QueueSummary(queue=queue, target=target, prompts=prompts, current=current, waiting=waiting, counts=counts)
