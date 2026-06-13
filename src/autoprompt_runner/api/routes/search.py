"""Search routes. Thin handlers over autoprompt_runner.search (SQLite LIKE)."""

from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from ... import search
from ..dependencies import get_db_path
from ..schemas import SearchAllResponse, SearchArtifactResult, SearchRunResult, SearchStepResult

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/runs", response_model=List[SearchRunResult])
def search_runs(
    q: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    db_path: str = Depends(get_db_path),
) -> List[SearchRunResult]:
    results = search.search_runs(db_path, query=q, status=status, provider=provider, limit=limit, offset=offset)
    return [SearchRunResult(**asdict(r)) for r in results]


@router.get("/artifacts", response_model=List[SearchArtifactResult])
def search_artifacts(
    q: Optional[str] = Query(default=None),
    artifact_type: Optional[str] = Query(default=None, alias="type"),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    db_path: str = Depends(get_db_path),
) -> List[SearchArtifactResult]:
    results = search.search_artifacts(db_path, query=q, artifact_type=artifact_type, limit=limit, offset=offset)
    return [SearchArtifactResult(**asdict(r)) for r in results]


@router.get("/all", response_model=SearchAllResponse)
def search_all(
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    db_path: str = Depends(get_db_path),
) -> SearchAllResponse:
    result = search.search_all(db_path, query=q, limit=limit, offset=offset)
    return SearchAllResponse(
        runs=[SearchRunResult(**asdict(r)) for r in result.runs],
        steps=[SearchStepResult(**asdict(s)) for s in result.steps],
        artifacts=[SearchArtifactResult(**asdict(a)) for a in result.artifacts],
    )
