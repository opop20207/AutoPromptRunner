"""Claude Code app prompt-queue routes. Thin handlers over autoprompt_runner.prompt_queue.

Queues hold ordered prompts bound to an app target. ``inject-current`` is always an explicit
action (the POST itself), returns the target summary + prompt status, and is rejected when the
queue is paused, a prompt is already WAITING_COMPLETION, or the target is disabled. The queue
never runs the Claude Code CLI -- it injects into the desktop app.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import List

from fastapi import APIRouter, Body, Depends, HTTPException

from ... import app_injection, prompt_queue, storage
from ..dependencies import get_db_path
from ..schemas import (
    InjectOutcomeResponse,
    InjectRequest,
    PromptQueueCreateRequest,
    PromptQueueResponse,
    QueuedPromptCreateRequest,
    QueuedPromptResponse,
    QueuedPromptUpdateRequest,
    QueueSummaryResponse,
    ReorderRequest,
)

router = APIRouter(prefix="/prompt-queues", tags=["prompt-queues"])

_STATUS = {"not_found": 404, "invalid": 400, "invalid_state": 409, "not_confirmed": 400, "mismatch": 409}
_INJECTION_STATUS = {"empty": 400, "disabled": 409, "not_found": 404, "clipboard": 409}


def _http(exc: prompt_queue.PromptQueueError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.kind, 400), detail=str(exc))


def _summary_resp(summary) -> QueueSummaryResponse:
    return QueueSummaryResponse(**asdict(summary))


@router.post("", response_model=PromptQueueResponse)
def create_prompt_queue(body: PromptQueueCreateRequest, db_path: str = Depends(get_db_path)) -> PromptQueueResponse:
    try:
        queue_obj = prompt_queue.create_queue(
            db_path, name=body.name, app_target_id=body.app_target_id,
            description=body.description, project_path=body.project_path,
        )
    except prompt_queue.PromptQueueError as exc:
        raise _http(exc)
    return PromptQueueResponse(**asdict(queue_obj))


@router.get("", response_model=List[PromptQueueResponse])
def list_prompt_queues(db_path: str = Depends(get_db_path)) -> List[PromptQueueResponse]:
    return [PromptQueueResponse(**asdict(q)) for q in prompt_queue.list_queues(db_path)]


@router.get("/{queue_id}", response_model=QueueSummaryResponse)
def get_prompt_queue(queue_id: int, db_path: str = Depends(get_db_path)) -> QueueSummaryResponse:
    try:
        summary = prompt_queue.build_queue_summary(db_path, queue_id)
    except prompt_queue.PromptQueueError as exc:
        raise _http(exc)
    return _summary_resp(summary)


@router.delete("/{queue_id}")
def delete_prompt_queue(queue_id: int, db_path: str = Depends(get_db_path)) -> dict:
    try:
        prompt_queue.delete_queue(db_path, queue_id)
    except prompt_queue.PromptQueueError as exc:
        raise _http(exc)
    return {"deleted": queue_id}


@router.post("/{queue_id}/prompts", response_model=QueuedPromptResponse)
def add_prompt(
    queue_id: int, body: QueuedPromptCreateRequest, db_path: str = Depends(get_db_path)
) -> QueuedPromptResponse:
    try:
        prompt_obj = prompt_queue.add_prompt_to_queue(
            db_path, queue_id, prompt=body.prompt, title=body.title, position=body.position
        )
    except prompt_queue.PromptQueueError as exc:
        raise _http(exc)
    return QueuedPromptResponse(**asdict(prompt_obj))


@router.patch("/prompts/{prompt_id}", response_model=QueuedPromptResponse)
def update_prompt(
    prompt_id: int, body: QueuedPromptUpdateRequest, db_path: str = Depends(get_db_path)
) -> QueuedPromptResponse:
    existing = storage.get_queued_prompt(db_path, prompt_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"queued prompt {prompt_id} not found")
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "prompt" in fields and not str(fields["prompt"]).strip():
        raise HTTPException(status_code=400, detail="prompt text must not be empty")
    if fields:
        storage.update_queued_prompt_text(
            db_path, prompt_id,
            title=fields["title"] if "title" in fields else storage._UNSET,
            prompt=fields["prompt"] if "prompt" in fields else storage._UNSET,
        )
    return QueuedPromptResponse(**asdict(storage.get_queued_prompt(db_path, prompt_id)))


@router.post("/prompts/{prompt_id}/reorder", response_model=QueuedPromptResponse)
def reorder_prompt(
    prompt_id: int, body: ReorderRequest, db_path: str = Depends(get_db_path)
) -> QueuedPromptResponse:
    try:
        prompt_obj = prompt_queue.reorder_prompt(db_path, prompt_id, body.new_position)
    except prompt_queue.PromptQueueError as exc:
        raise _http(exc)
    return QueuedPromptResponse(**asdict(prompt_obj))


@router.post("/{queue_id}/inject-current", response_model=InjectOutcomeResponse)
def inject_current(
    queue_id: int, body: InjectRequest = Body(default=InjectRequest()), db_path: str = Depends(get_db_path)
) -> InjectOutcomeResponse:
    try:
        outcome = prompt_queue.inject_current_prompt(
            db_path, queue_id, user_confirmed=body.user_confirmed,
            allow_target_mismatch=body.allow_target_mismatch,
            restore_clipboard_after=body.restore_clipboard_after, dry_run=body.dry_run,
        )
    except prompt_queue.PromptQueueError as exc:
        raise _http(exc)
    except app_injection.InjectionError as exc:
        raise HTTPException(status_code=_INJECTION_STATUS.get(exc.kind, 400), detail=str(exc))
    return InjectOutcomeResponse(**asdict(outcome))


def _queue_action(action, queue_id: int, db_path: str) -> QueueSummaryResponse:
    try:
        summary = action(db_path, queue_id)
    except prompt_queue.PromptQueueError as exc:
        raise _http(exc)
    return _summary_resp(summary)


@router.post("/{queue_id}/complete-current", response_model=QueueSummaryResponse)
def complete_current(queue_id: int, db_path: str = Depends(get_db_path)) -> QueueSummaryResponse:
    return _queue_action(prompt_queue.mark_current_complete, queue_id, db_path)


@router.post("/{queue_id}/skip-current", response_model=QueueSummaryResponse)
def skip_current(queue_id: int, db_path: str = Depends(get_db_path)) -> QueueSummaryResponse:
    return _queue_action(prompt_queue.skip_current_prompt, queue_id, db_path)


@router.post("/{queue_id}/pause", response_model=QueueSummaryResponse)
def pause_queue(queue_id: int, db_path: str = Depends(get_db_path)) -> QueueSummaryResponse:
    return _queue_action(prompt_queue.pause_queue, queue_id, db_path)


@router.post("/{queue_id}/resume", response_model=QueueSummaryResponse)
def resume_queue(queue_id: int, db_path: str = Depends(get_db_path)) -> QueueSummaryResponse:
    return _queue_action(prompt_queue.resume_queue, queue_id, db_path)


@router.post("/{queue_id}/cancel", response_model=QueueSummaryResponse)
def cancel_queue(queue_id: int, db_path: str = Depends(get_db_path)) -> QueueSummaryResponse:
    return _queue_action(prompt_queue.cancel_queue, queue_id, db_path)
