"""Comparison routes. Thin handlers over autoprompt_runner.compare (stored DB content)."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query

from ... import compare
from ..dependencies import get_db_path
from ..schemas import RunComparisonResponse

router = APIRouter(prefix="/compare", tags=["compare"])


@router.get("/runs", response_model=RunComparisonResponse)
def compare_runs(
    run_a: int = Query(...),
    run_b: int = Query(...),
    show_prompts: bool = Query(default=False),
    show_artifacts: bool = Query(default=True),
    db_path: str = Depends(get_db_path),
) -> RunComparisonResponse:
    try:
        result = compare.compare_runs(
            db_path, run_a, run_b, show_prompts=show_prompts, show_artifacts=show_artifacts
        )
    except compare.CompareError as exc:
        raise HTTPException(status_code=404 if exc.kind == "not_found" else 400, detail=str(exc))
    return RunComparisonResponse(**asdict(result))
