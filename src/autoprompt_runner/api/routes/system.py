"""System routes: stale-state status, reconciliation, and worker heartbeats.

Thin handlers over autoprompt_runner.reconcile (crash / restart recovery). Reconciliation is
non-destructive: only database rows change -- no files are deleted and no Git command is run.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import List

from fastapi import APIRouter, Depends

from ... import reconcile, storage
from ..dependencies import get_db_path
from ..schemas import (
    ReconcileRequest,
    ReconciliationReportResponse,
    SystemStatusResponse,
    WorkerHeartbeatResponse,
)

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status", response_model=SystemStatusResponse)
def system_status(db_path: str = Depends(get_db_path)) -> SystemStatusResponse:
    return SystemStatusResponse(**asdict(reconcile.build_system_status(db_path)))


@router.post("/reconcile", response_model=ReconciliationReportResponse)
def system_reconcile(
    body: ReconcileRequest = ReconcileRequest(), db_path: str = Depends(get_db_path)
) -> ReconciliationReportResponse:
    report = reconcile.reconcile_stale_state(db_path, dry_run=body.dry_run)
    return ReconciliationReportResponse(**asdict(report))


@router.get("/workers", response_model=List[WorkerHeartbeatResponse])
def system_workers(db_path: str = Depends(get_db_path)) -> List[WorkerHeartbeatResponse]:
    return [WorkerHeartbeatResponse(**asdict(hb)) for hb in storage.list_worker_heartbeats(db_path)]
