"""Search across stored runs, steps, and artifacts.

A small, dependency-free search over the local SQLite database: it uses ``LIKE`` queries
(case-insensitive for ASCII) against the already-stored content -- run prompts, step
stdout/stderr/prompts, and artifact type/content/path -- and never reads files from disk,
never runs an external search engine, and does no semantic matching. Results are compact
(small previews around the match), so huge artifact content is never returned here; the
full content is fetched separately via the artifact endpoints/commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import storage

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_PREVIEW_WINDOW = 140
_PREVIEW_FALLBACK = 300


@dataclass
class RunSearchResult:
    id: int
    status: str
    provider: str
    created_at: str
    prompt_preview: str


@dataclass
class StepSearchResult:
    id: int
    run_id: int
    loop_index: int
    status: str
    exit_code: Optional[int]
    match_field: str
    match_preview: str


@dataclass
class ArtifactSearchResult:
    id: int
    run_id: int
    step_id: Optional[int]
    type: str
    created_at: str
    match_field: str
    match_preview: str


@dataclass
class ChangedFileSearchResult:
    run_id: int
    step_id: Optional[int]
    path: str


@dataclass
class SearchAllResult:
    runs: List[RunSearchResult]
    steps: List[StepSearchResult]
    artifacts: List[ArtifactSearchResult]


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _clamp_limit(limit) -> int:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    if limit < 1:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


def _offset(offset) -> int:
    try:
        return max(0, int(offset))
    except (TypeError, ValueError):
        return 0


def _normalize(text: Optional[str]) -> str:
    """Collapse all whitespace/newlines into single spaces for a compact preview."""
    return " ".join((text or "").split())


def _preview(content: Optional[str], query: Optional[str] = None) -> str:
    """A compact preview: a window around the match, or the first chars if no match."""
    if not content:
        return ""
    if query:
        idx = content.lower().find(query.lower())
        if idx >= 0:
            start = max(0, idx - _PREVIEW_WINDOW // 2)
            end = min(len(content), idx + len(query) + _PREVIEW_WINDOW // 2)
            snippet = _normalize(content[start:end])
            return ("…" if start > 0 else "") + snippet + ("…" if end < len(content) else "")
    snippet = _normalize(content[:_PREVIEW_FALLBACK])
    return snippet + ("…" if len(content) > _PREVIEW_FALLBACK else "")


def _step_match(step, query: Optional[str]):
    fields = [("prompt", step.prompt), ("stdout", step.stdout), ("stderr", step.stderr), ("next_prompt", step.next_prompt)]
    if query:
        needle = query.lower()
        for name, value in fields:
            if value and needle in value.lower():
                return name, _preview(value, query)
    return "prompt", _preview(step.prompt, query)


def _artifact_match(artifact, query: Optional[str]):
    fields = [("content", artifact.content), ("path", artifact.path), ("type", artifact.type)]
    if query:
        needle = query.lower()
        for name, value in fields:
            if value and needle in value.lower():
                return name, _preview(value, query)
    return "content", _preview(artifact.content, query)


def search_runs(
    db_path: str,
    query: Optional[str] = None,
    status: Optional[str] = None,
    provider: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> List[RunSearchResult]:
    query = _clean(query)
    runs = storage.search_runs(
        db_path, query=query, status=_clean(status), provider=_clean(provider),
        date_from=_clean(date_from), date_to=_clean(date_to),
        limit=_clamp_limit(limit), offset=_offset(offset),
    )
    return [
        RunSearchResult(
            id=run.id, status=run.status, provider=run.provider, created_at=run.created_at,
            prompt_preview=_preview(run.root_prompt, query),
        )
        for run in runs
    ]


def search_steps(
    db_path: str,
    query: Optional[str] = None,
    run_id: Optional[int] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> List[StepSearchResult]:
    query = _clean(query)
    steps = storage.search_steps(db_path, query=query, run_id=run_id, limit=_clamp_limit(limit), offset=_offset(offset))
    results = []
    for step in steps:
        field, preview = _step_match(step, query)
        results.append(
            StepSearchResult(
                id=step.id, run_id=step.run_id, loop_index=step.loop_index, status=step.status,
                exit_code=step.exit_code, match_field=field, match_preview=preview,
            )
        )
    return results


def search_artifacts(
    db_path: str,
    query: Optional[str] = None,
    artifact_type: Optional[str] = None,
    run_id: Optional[int] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> List[ArtifactSearchResult]:
    query = _clean(query)
    artifacts = storage.search_artifacts(
        db_path, query=query, artifact_type=_clean(artifact_type), run_id=run_id,
        limit=_clamp_limit(limit), offset=_offset(offset),
    )
    results = []
    for artifact in artifacts:
        field, preview = _artifact_match(artifact, query)
        results.append(
            ArtifactSearchResult(
                id=artifact.id, run_id=artifact.run_id, step_id=artifact.step_id, type=artifact.type,
                created_at=artifact.created_at, match_field=field, match_preview=preview,
            )
        )
    return results


def search_changed_files(
    db_path: str,
    path_query: Optional[str] = None,
    run_id: Optional[int] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> List[ChangedFileSearchResult]:
    """Search changed-file paths recorded in ``changed_files`` artifacts (no disk reads)."""
    query = _clean(path_query)
    artifacts = storage.search_artifacts(
        db_path, query=query, artifact_type="changed_files", run_id=run_id,
        limit=_clamp_limit(limit), offset=_offset(offset),
    )
    results = []
    for artifact in artifacts:
        for line in (artifact.content or "").splitlines():
            path = line.strip()
            if path and (query is None or query.lower() in path.lower()):
                results.append(ChangedFileSearchResult(run_id=artifact.run_id, step_id=artifact.step_id, path=path))
    return results


def search_all(db_path: str, query: Optional[str] = None, limit: int = DEFAULT_LIMIT, offset: int = 0) -> SearchAllResult:
    return SearchAllResult(
        runs=search_runs(db_path, query=query, limit=limit, offset=offset),
        steps=search_steps(db_path, query=query, limit=limit, offset=offset),
        artifacts=search_artifacts(db_path, query=query, limit=limit, offset=offset),
    )
