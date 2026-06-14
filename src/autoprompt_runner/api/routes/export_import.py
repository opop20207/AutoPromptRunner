"""Export / import routes. Thin handlers over autoprompt_runner.export_import.

Export returns the JSON payload in the response (no server-side file is written); import
validates and applies a payload (never deleting existing data). Reads only stored database
content and redacts secret-like artifact content by default.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ... import export_import
from ..dependencies import get_db_path
from ..schemas import (
    ExportPayloadResponse,
    ExportRequest,
    ExportSummaryResponse,
    ImportRequest,
    ImportSummaryResponse,
)

router = APIRouter(prefix="/export-import", tags=["export-import"])


@router.post("/export", response_model=ExportPayloadResponse)
def export_data(body: ExportRequest, db_path: str = Depends(get_db_path)) -> ExportPayloadResponse:
    payload = export_import.build_export_payload(
        db_path,
        include_projects=body.include_projects,
        include_providers=body.include_providers,
        include_templates=body.include_templates,
        include_runs=body.include_runs,
        include_artifacts=body.include_artifacts,
        include_recoveries=body.include_recoveries,
        run_ids=body.run_ids or None,
        project_names=body.project_names or None,
        artifact_content=body.artifact_content,
        redact_sensitive=body.redact_sensitive,
    )
    return ExportPayloadResponse(**payload)


@router.post("/import", response_model=ImportSummaryResponse)
def import_data(body: ImportRequest, db_path: str = Depends(get_db_path)) -> ImportSummaryResponse:
    try:
        result = export_import.import_export_payload(db_path, body.payload, mode=body.mode)
    except export_import.ExportImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ImportSummaryResponse(**result)


@router.post("/summary", response_model=ExportSummaryResponse)
def summary(body: ImportRequest) -> ExportSummaryResponse:
    return ExportSummaryResponse(**export_import.summarize_export(body.payload))
