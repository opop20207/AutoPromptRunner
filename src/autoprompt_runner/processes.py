"""Local in-memory registry of running agent subprocesses.

When a runner launches an external agent (Claude Code / Codex) it registers the
``subprocess.Popen`` here keyed by ``run_id`` so a cancellation can terminate it. The
registry is **local to the current process** (the worker that launched the process) and
does **not** survive a restart -- cancelling a run from a different process (e.g. the API
server) cannot reach the worker's subprocess, so force-stopping a running external process
is best-effort. Cancellation of queued / waiting runs is always deterministic and does not
depend on this registry.

Termination is graceful first (``terminate``), then a forced ``kill`` only if the process
is still alive after a grace period. Never uses a shell. A missing process is handled
safely (no error).
"""

from __future__ import annotations

import subprocess
import threading
from typing import Dict, Set

_lock = threading.Lock()
_processes: Dict[int, "subprocess.Popen"] = {}
# run_ids that this process terminated, so a runner can report a cancellation result.
_terminated: Set[int] = set()


def register_process(run_id: int, process: "subprocess.Popen") -> None:
    """Register a launched subprocess for ``run_id``."""
    with _lock:
        _processes[int(run_id)] = process


def get_process(run_id: int):
    """Return the registered process for ``run_id`` or ``None``."""
    with _lock:
        return _processes.get(int(run_id))


def unregister_process(run_id: int) -> None:
    """Remove ``run_id`` from the registry (safe if absent)."""
    with _lock:
        _processes.pop(int(run_id), None)


def was_terminated(run_id: int) -> bool:
    """Return True if this process terminated ``run_id`` (set by :func:`terminate_process`)."""
    with _lock:
        return int(run_id) in _terminated


def clear_terminated(run_id: int) -> None:
    """Clear the terminated flag for ``run_id`` (safe if absent)."""
    with _lock:
        _terminated.discard(int(run_id))


def terminate_process(run_id: int, grace_seconds: int = 5) -> bool:
    """Terminate the process registered for ``run_id``; return True if one was signaled.

    Sends a graceful ``terminate`` first and escalates to ``kill`` only if the process is
    still alive after ``grace_seconds``. Returns False (safely) when no live process is
    registered -- which is also the cross-process case where the run executes in a
    different process than this one.
    """
    process = get_process(run_id)
    if process is None:
        return False
    if process.poll() is not None:
        return False  # already exited
    with _lock:
        _terminated.add(int(run_id))
    process.terminate()  # graceful (SIGTERM / Windows TerminateProcess)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()  # force only after the grace period
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            pass
    return True
