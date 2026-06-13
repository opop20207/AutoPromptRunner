"""Local background worker that executes queued runs.

``LocalWorker`` polls the local run queue, claims one job at a time, executes the run
through :class:`RunService` (so the safety checks, workspace locks, Git artifact capture,
and prompt generation all still apply), and records the job outcome. It is a single local
loop -- not a distributed worker pool. An exception during a run is caught and recorded as
a FAILED job so the loop keeps running, and the idle sleep between polls is interruptible
(Ctrl+C / :meth:`stop`).
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from . import queue, storage
from .services.run_service import RunService

DEFAULT_POLL_INTERVAL_SECONDS = 2.0


class LocalWorker:
    def __init__(
        self,
        db_path: Optional[str] = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        service: Optional[RunService] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.db_path = storage.init_db(db_path)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.service = service or RunService(self.db_path)
        self.log = log or (lambda msg: print(msg))
        self._stop = threading.Event()

    def run_once(self) -> bool:
        """Claim and execute one queued job. Returns True if a job ran, False if none."""
        job = queue.claim_next_job(self.db_path)
        if job is None:
            return False
        self.log(f"worker: running job {job.id} (run {job.run_id})")
        try:
            report = self.service.execute_queued_run(job.run_id)
            queue.complete_job(self.db_path, job.id)
            self.log(f"worker: job {job.id} done (run {job.run_id} -> {report.run_status})")
        except Exception as exc:  # noqa: BLE001  (record any failure and keep the loop alive)
            queue.fail_job(self.db_path, job.id, exc)
            self.log(f"worker: job {job.id} failed (run {job.run_id}): {exc}")
        return True

    def run_forever(self, stop_after: Optional[int] = None) -> int:
        """Poll the queue and execute jobs until stopped. Returns the number executed.

        ``stop_after`` (used by tests) stops the loop after that many jobs, or once the
        queue is empty -- whichever comes first.
        """
        executed = 0
        while not self._stop.is_set():
            ran = self.run_once()
            if ran:
                executed += 1
                if stop_after is not None and executed >= stop_after:
                    break
                continue  # drain consecutive jobs without sleeping
            if stop_after is not None:
                break  # bounded mode: an empty queue ends the loop
            self._stop.wait(self.poll_interval_seconds)  # interruptible idle sleep
        return executed

    def stop(self) -> None:
        """Signal the polling loop to stop after the current job."""
        self._stop.set()
