"""Prompt chain routes. Thin handlers over autoprompt_runner.chains (stored DB content)."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query

from ... import chains
from ..dependencies import get_db_path
from ..schemas import PromptChainResponse

router = APIRouter(prefix="/chains", tags=["chains"])


@router.get("/runs/{run_id}", response_model=PromptChainResponse)
def get_run_chain(
    run_id: int,
    full_prompts: bool = Query(default=False),
    include_artifacts: bool = Query(default=True),
    errors_only: bool = Query(default=False),
    db_path: str = Depends(get_db_path),
) -> PromptChainResponse:
    try:
        chain = chains.build_prompt_chain(
            db_path, run_id,
            full_prompts=full_prompts, include_artifacts=include_artifacts, errors_only=errors_only,
        )
    except chains.ChainError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return PromptChainResponse(**asdict(chain))
