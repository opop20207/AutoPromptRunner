"""Local background worker that executes queued runs.

``LocalWorker`` polls the local run queue, claims one job at a time, executes the run
through :class:`RunService` (so the safety checks, workspace locks, Git artifact capture,
and prompt generation all still apply), and records the job outcome. It is a single local
loop -- not a distributed worker pool. An exception during a run is caught and recorded as
a FAILED job so the loop keeps running, and the idle sleep between polls is interruptible
(Ctrl+C / :meth:`stop`).
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

from . import events, queue, settings, storage
from .services.run_service import RunService
from .state import RunStatus

DEFAULT_POLL_INTERVAL_SECONDS = 2.0

# Run statuses for which a claimed job must not be executed (already cancelled/finished).
_TERMINAL_RUN_STATUSES = {RunStatus.DONE.value, RunStatus.FAILED.value, RunStatus.STOPPED.value}


class LocalWorker:
    def __init__(
        self,
        db_path: Optional[str] = None,
        poll_interval_seconds: Optional[float] = None,
        service: Optional[RunService] = None,
        log: Optional[Callable[[str], None]] = None,
        reconcile_on_start: bool = True,
        worker_id: Optional[str] = None,
    ) -> None:
        self.db_path = storage.init_db(db_path)
        # Default the poll interval from settings (config file + env); the CLI passes an
        # explicit value only when --poll-interval-seconds was given.
        if poll_interval_seconds is None:
            poll_interval_seconds = settings.load_settings().queue.poll_interval_seconds
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.service = service or RunService(self.db_path)
        self.log = log or (lambda msg: print(msg))
        self.reconcile_on_start = reconcile_on_start
        self.worker_id = worker_id or f"worker-{os.getpid()}"
        self._heartbeat_id: Optional[int] = None
        self._stop = threading.Event()

    def run_once(self) -> bool:
        """Claim and execute one queued job. Returns True if a job ran, False if none."""
        job = queue.claim_next_job(self.db_path)
        if job is None:
            return False
        # Skip a job whose run was cancelled/stopped between enqueue and claim.
        run = storage.get_run(self.db_path, job.run_id)
        if run is None or run.status in _TERMINAL_RUN_STATUSES:
            status = run.status if run is not None else "missing"
            queue.fail_job(self.db_path, job.id, f"run {job.run_id} is {status}; not executed")
            self.log(f"worker: job {job.id} skipped (run {job.run_id} is {status})")
            return True
        self.log(f"worker: running job {job.id} (run {job.run_id})")
        self._emit(job.run_id, f"worker started job {job.id}")
        try:
            report = self.service.execute_queued_run(job.run_id)
            queue.complete_job(self.db_path, job.id)
            self.log(f"worker: job {job.id} done (run {job.run_id} -> {report.run_status})")
            self._emit(job.run_id, f"worker finished job {job.id} ({report.run_status})")
        except Exception as exc:  # noqa: BLE001  (record any failure and keep the loop alive)
            queue.fail_job(self.db_path, job.id, exc)
            self.log(f"worker: job {job.id} failed (run {job.run_id}): {exc}")
            self._emit(job.run_id, f"worker job {job.id} failed: {exc}")
        return True

    def _emit(self, run_id: int, message: str) -> None:
        """Emit a worker_message run event (best-effort; never breaks the worker loop)."""
        try:
            events.create_event(self.db_path, run_id, events.WORKER_MESSAGE, message=message)
        except Exception:  # noqa: BLE001
            pass

    def run_forever(self, stop_after: Optional[int] = None) -> int:
        """Poll the queue and execute jobs until stopped. Returns the number executed.

        On start it (optionally) reconciles stale state left by a previous crash and then
        registers a heartbeat, refreshing it each poll cycle and marking it STOPPED on a clean
        exit. ``stop_after`` (used by tests / ``--once``) stops after that many jobs, or once
        the queue is empty -- whichever comes first.
        """
        self._begin_session()
        executed = 0
        try:
            while not self._stop.is_set():
                self._beat()
                # A transient per-cycle error (e.g. a momentary SQLite file lock, which is
                # stricter on Windows) must not kill a long-running worker. ``run_once`` already
                # records a failed *job*; this guards the claim/poll itself. Returns to idle.
                try:
                    ran = self.run_once()
                except Exception as exc:  # noqa: BLE001 - keep the worker loop alive
                    self.log(f"worker: poll error (continuing): {exc}")
                    ran = False
                if ran:
                    executed += 1
                    if stop_after is not None and executed >= stop_after:
                        break
                    continue  # drain consecutive jobs without sleeping
                if stop_after is not None:
                    break  # bounded mode: an empty queue ends the loop
                self._stop.wait(self.poll_interval_seconds)  # interruptible idle sleep
        finally:
            self._end_session()
        return executed

    def _begin_session(self) -> None:
        """Reconcile stale state (before registering, so a crashed worker's jobs are seen),
        then create this worker's heartbeat."""
        if self.reconcile_on_start:
            try:
                report = self.service.reconcile_stale_state()
                if report.actions:
                    self.log(
                        f"worker: reconciled {report.stale_runs} run(s), {report.stale_queue_jobs} job(s), "
                        f"{report.stale_locks} lock(s)"
                    )
            except Exception as exc:  # noqa: BLE001 - reconciliation must never block startup
                self.log(f"worker: reconcile-on-start failed: {exc}")
        try:
            self._heartbeat_id = storage.create_worker_heartbeat(self.db_path, self.worker_id)
        except Exception:  # noqa: BLE001
            self._heartbeat_id = None

    def _beat(self) -> None:
        """Refresh the worker heartbeat (best-effort)."""
        if self._heartbeat_id is not None:
            try:
                storage.update_worker_heartbeat(self.db_path, self._heartbeat_id)
            except Exception:  # noqa: BLE001
                pass

    def _end_session(self) -> None:
        """Mark this worker's heartbeat STOPPED on a clean shutdown (best-effort)."""
        if self._heartbeat_id is not None:
            try:
                storage.stop_worker_heartbeat(self.db_path, self._heartbeat_id)
            except Exception:  # noqa: BLE001
                pass
            self._heartbeat_id = None

    def stop(self) -> None:
        """Signal the polling loop to stop after the current job."""
        self._stop.set()
