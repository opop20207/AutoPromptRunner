"""Run event routes: a JSON event list and a Server-Sent Events (SSE) live stream.

The SSE stream is local-first: it streams a run's events by polling the SQLite event store
(so it works even when the worker runs in a separate process from the API), sends a
heartbeat comment periodically, and closes once the run reaches a terminal state. No
WebSocket and no external message broker are used.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from ... import events as events_mod
from ... import storage
from ...state import TERMINAL_STATUSES
from ..dependencies import get_db_path, require_api_auth, require_api_auth_sse
from ..schemas import RunEventListResponse, RunEventResponse

router = APIRouter(prefix="/events", tags=["events"])

# How often the SSE stream polls the event store, and how often it emits a heartbeat comment.
_POLL_INTERVAL_SECONDS = 0.5
_HEARTBEAT_SECONDS = 15.0
_BATCH = 200


def _to_response(event) -> RunEventResponse:
    return RunEventResponse(**events_mod.serialize_event(event))


@router.get("/runs/{run_id}", response_model=RunEventListResponse, dependencies=[Depends(require_api_auth)])
def list_run_events(
    run_id: int,
    after_id: Optional[int] = Query(default=None),
    limit: int = Query(default=100),
    db_path: str = Depends(get_db_path),
) -> RunEventListResponse:
    if storage.get_run(db_path, run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    items = storage.list_run_events(db_path, run_id, after_id=after_id, limit=limit)
    return RunEventListResponse(
        events=[_to_response(e) for e in items],
        latest_id=items[-1].id if items else after_id,
    )


def _event_stream(db_path: str, run_id: int, after_id: Optional[int]):
    """Yield SSE chunks for a run: catch up from ``after_id``, then poll for new events."""
    last_id = after_id or 0
    last_send = time.monotonic()
    while True:
        batch = storage.list_run_events(db_path, run_id, after_id=last_id, limit=_BATCH)
        for event in batch:
            yield events_mod.format_sse_event(event)
            last_id = event.id
            last_send = time.monotonic()

        run = storage.get_run(db_path, run_id)
        if run is None:
            yield ": run-removed\n\n"
            return
        if run.status in TERMINAL_STATUSES:
            # Drain any final events written between the last poll and the status check.
            for event in storage.list_run_events(db_path, run_id, after_id=last_id, limit=_BATCH):
                yield events_mod.format_sse_event(event)
                last_id = event.id
            yield ": stream-end\n\n"
            return

        now = time.monotonic()
        if now - last_send >= _HEARTBEAT_SECONDS:
            yield ": heartbeat\n\n"
            last_send = now
        time.sleep(_POLL_INTERVAL_SECONDS)


@router.get("/runs/{run_id}/stream", dependencies=[Depends(require_api_auth_sse)])
def stream_run_events(
    run_id: int,
    after_id: Optional[int] = Query(default=None),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    db_path: str = Depends(get_db_path),
) -> StreamingResponse:
    if storage.get_run(db_path, run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    # On reconnect, EventSource resends the last id it saw via the Last-Event-ID header; use
    # it (when an explicit after_id was not given) so the stream resumes without replaying.
    start_after = after_id
    if start_after is None and last_event_id:
        try:
            start_after = int(last_event_id)
        except ValueError:
            start_after = None
    return StreamingResponse(
        _event_stream(db_path, run_id, start_after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
