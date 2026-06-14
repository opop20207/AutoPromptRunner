"""Run events: a local-first event model for live log streaming (SSE).

Every event is persisted to SQLite (so a client can replay history via ``after_id``) and
also published to an in-process :class:`RunEventBus` for same-process subscribers. The SSE
endpoint streams events by polling the database, so it works even when the worker runs in a
separate process from the API -- no Redis or external message broker is required, and no
WebSocket is used.

Standard library only. Events never carry secrets or auth tokens -- emitters pass only the
non-secret message/payload, and stdout/stderr come from the captured runner output.
"""

from __future__ import annotations

import json
import queue as _stdqueue
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from . import storage

# Event types (one per lifecycle point; also used as the SSE ``event:`` field).
RUN_CREATED = "run_created"
RUN_QUEUED = "run_queued"
RUN_STARTED = "run_started"
STEP_STARTED = "step_started"
STDOUT = "stdout"
STDERR = "stderr"
STEP_FINISHED = "step_finished"
APPROVAL_PENDING = "approval_pending"
RUN_DONE = "run_done"
RUN_FAILED = "run_failed"
RUN_STOPPED = "run_stopped"
CANCELLATION_REQUESTED = "cancellation_requested"
SAFETY_WARNING = "safety_warning"
LOCK_ACQUIRED = "lock_acquired"
LOCK_RELEASED = "lock_released"
WORKER_MESSAGE = "worker_message"

# Reconciliation events (stale-state recovery; see autoprompt_runner.reconcile). The two
# reconciliation_* events are system-scoped and recorded under SYSTEM_RUN_ID.
RECONCILIATION_STARTED = "reconciliation_started"
RECONCILIATION_FINISHED = "reconciliation_finished"
STALE_RUN_FAILED = "stale_run_failed"
STALE_LOCK_EXPIRED = "stale_lock_expired"
STALE_JOB_FAILED = "stale_job_failed"

# Run id used for system-scoped events that do not belong to a single run.
SYSTEM_RUN_ID = 0

# Terminal event types: once one is sent, the SSE stream for that run can close.
TERMINAL_EVENT_TYPES = frozenset({RUN_DONE, RUN_FAILED, RUN_STOPPED})

# Cap message length stored/streamed per event (keeps the event bus and DB light).
_MESSAGE_CAP = 8000


@dataclass
class RunEvent:
    """A single run event row (``payload`` is a raw JSON string, or None)."""

    id: int
    run_id: int
    step_id: Optional[int]
    type: str
    message: Optional[str]
    payload: Optional[str]
    created_at: str


class RunEventBus:
    """A tiny thread-safe in-process pub/sub of :class:`RunEvent` keyed by run id.

    Subscribers (e.g. a same-process SSE handler or a test) get a queue of events for one
    run. The SSE endpoint itself streams from the database (so it also sees events emitted by
    a separate worker process); this bus is the low-latency same-process path.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[int, Set["_stdqueue.Queue"]] = {}
        self._lock = threading.Lock()

    def subscribe(self, run_id: int) -> "_stdqueue.Queue":
        q: "_stdqueue.Queue" = _stdqueue.Queue()
        with self._lock:
            self._subscribers.setdefault(run_id, set()).add(q)
        return q

    def unsubscribe(self, run_id: int, q: "_stdqueue.Queue") -> None:
        with self._lock:
            subs = self._subscribers.get(run_id)
            if subs:
                subs.discard(q)
                if not subs:
                    self._subscribers.pop(run_id, None)

    def publish(self, event: RunEvent) -> None:
        with self._lock:
            subs = list(self._subscribers.get(event.run_id, ()))
        for q in subs:
            try:
                q.put_nowait(event)
            except Exception:  # pragma: no cover - never let a slow subscriber break emission
                pass


# Process-wide event bus.
BUS = RunEventBus()


def _clip(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= _MESSAGE_CAP else text[:_MESSAGE_CAP] + "…"


def create_event(
    db_path: str,
    run_id: int,
    type: str,
    message: Optional[str] = None,
    payload: Optional[dict] = None,
    step_id: Optional[int] = None,
) -> RunEvent:
    """Persist an event and publish it to in-process subscribers. Returns the RunEvent.

    ``payload`` is an optional JSON-serializable dict; secrets must never be placed in it.
    """
    payload_text = json.dumps(payload) if payload is not None else None
    event = storage.create_run_event(
        db_path, run_id=run_id, type=type, message=_clip(message), payload=payload_text, step_id=step_id
    )
    BUS.publish(event)
    return event


def parse_event_payload(event: RunEvent) -> dict:
    """Return the event payload parsed into a dict (empty dict when absent/invalid)."""
    if not event.payload:
        return {}
    try:
        value = json.loads(event.payload)
        return value if isinstance(value, dict) else {"value": value}
    except (json.JSONDecodeError, TypeError):
        return {}


def serialize_event(event: RunEvent) -> dict:
    """Return a JSON-safe dict for an event's SSE ``data`` field (no secrets)."""
    return {
        "id": event.id,
        "run_id": event.run_id,
        "step_id": event.step_id,
        "type": event.type,
        "message": event.message,
        "payload": parse_event_payload(event),
        "created_at": event.created_at,
    }


def format_sse_event(event: RunEvent) -> str:
    """Render an event as a standard text/event-stream chunk (``id`` / ``event`` / ``data``)."""
    data = json.dumps(serialize_event(event))
    return f"id: {event.id}\nevent: {event.type}\ndata: {data}\n\n"


def list_events(db_path: str, run_id: int, after_id: Optional[int] = None, limit: int = 100) -> List[RunEvent]:
    """Convenience pass-through to storage for stored events (used by the SSE endpoint)."""
    return storage.list_run_events(db_path, run_id, after_id=after_id, limit=limit)
