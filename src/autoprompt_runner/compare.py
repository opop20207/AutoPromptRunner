"""Compare two stored runs.

A small, dependency-free comparison over the local SQLite database. It loads two runs and
their stored steps and artifacts and reports the differences -- run metadata, prompts, step
results, changed files, diff stats, artifact counts, and the latest generated next prompts.

Like ``search``, it reads only already-stored database content: it never reads workspace
files from disk, never calls an external tool or diff engine, does no semantic comparison,
and never surfaces secret-file contents (only the changed-file *paths* recorded in the
``changed_files`` artifacts). Comparisons are deterministic: results depend only on the
stored rows, ordered by step/loop index.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import storage
from .artifacts import ArtifactType

# Previews are intentionally short; full text is only included when explicitly requested.
_PREVIEW = 300
# Diff-stat text is returned raw but capped so a huge stat never bloats the response.
_DIFF_STAT_CAP = 4000


class CompareError(Exception):
    """Raised when a comparison cannot be performed.

    ``kind`` is ``"not_found"`` (a run id does not exist) or ``"same_run"`` (the two ids
    are equal); callers map it to a CLI exit code or an HTTP status (404 / 400).
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class RunSide:
    """One run's metadata in a comparison (the ``a`` or ``b`` side)."""

    id: int
    status: str
    provider: str
    created_at: str
    root_prompt_preview: str
    root_prompt: Optional[str] = None  # full text, only when show_prompts is set


@dataclass
class MetadataComparison:
    run_a: RunSide
    run_b: RunSide
    same_provider: bool
    same_status: bool


@dataclass
class StepComparison:
    step_count_a: int
    step_count_b: int
    exit_codes_a: List[Optional[int]]
    exit_codes_b: List[Optional[int]]
    failed_steps_a: int
    failed_steps_b: int


@dataclass
class ChangedFilesComparison:
    only_a: List[str]
    only_b: List[str]
    common: List[str]
    warning: Optional[str] = None


@dataclass
class ArtifactCounts:
    counts: Dict[str, int] = field(default_factory=dict)


@dataclass
class RunComparison:
    run_a: RunSide
    run_b: RunSide
    same_provider: bool
    same_status: bool
    steps: StepComparison
    changed_files: ChangedFilesComparison
    diff_stat_a: str
    diff_stat_b: str
    latest_next_prompt_a: str
    latest_next_prompt_b: str
    artifact_counts_by_type_a: ArtifactCounts
    artifact_counts_by_type_b: ArtifactCounts
    summary: str
    latest_next_prompt_full_a: Optional[str] = None
    latest_next_prompt_full_b: Optional[str] = None


def _preview(text: Optional[str], limit: int = _PREVIEW) -> str:
    """Collapse whitespace and truncate to a compact, single-line preview."""
    norm = " ".join((text or "").split())
    return norm if len(norm) <= limit else norm[:limit] + "…"


def _is_failed_step(step) -> bool:
    if (step.status or "").upper() == "FAILED":
        return True
    return step.exit_code is not None and step.exit_code != 0


def _changed_paths(artifacts) -> set:
    """Union of changed-file paths across a run's ``changed_files`` artifacts (no disk reads)."""
    paths = set()
    for artifact in artifacts:
        if artifact.type != ArtifactType.CHANGED_FILES.value:
            continue
        for line in (artifact.content or "").splitlines():
            path = line.strip()
            if path:
                paths.add(path)
    return paths


def _latest_content(artifacts, artifact_type: str) -> str:
    """The last (most recent) stored content for ``artifact_type``, or empty string."""
    content = ""
    for artifact in artifacts:
        if artifact.type == artifact_type and artifact.content:
            content = artifact.content
    return content


def compare_run_metadata(run_a, run_b, show_prompts: bool = False) -> MetadataComparison:
    """Compare two runs' top-level metadata (status, provider, created_at, root prompt)."""
    side_a = RunSide(
        id=run_a.id, status=run_a.status, provider=run_a.provider, created_at=run_a.created_at,
        root_prompt_preview=_preview(run_a.root_prompt),
        root_prompt=run_a.root_prompt if show_prompts else None,
    )
    side_b = RunSide(
        id=run_b.id, status=run_b.status, provider=run_b.provider, created_at=run_b.created_at,
        root_prompt_preview=_preview(run_b.root_prompt),
        root_prompt=run_b.root_prompt if show_prompts else None,
    )
    return MetadataComparison(
        run_a=side_a, run_b=side_b,
        same_provider=run_a.provider == run_b.provider,
        same_status=run_a.status == run_b.status,
    )


def compare_steps(steps_a, steps_b) -> StepComparison:
    """Compare step counts, exit codes (in order), and failed-step counts."""
    return StepComparison(
        step_count_a=len(steps_a),
        step_count_b=len(steps_b),
        exit_codes_a=[s.exit_code for s in steps_a],
        exit_codes_b=[s.exit_code for s in steps_b],
        failed_steps_a=sum(1 for s in steps_a if _is_failed_step(s)),
        failed_steps_b=sum(1 for s in steps_b if _is_failed_step(s)),
    )


def compare_changed_files(artifacts_a, artifacts_b) -> ChangedFilesComparison:
    """Compare changed-file paths using ``changed_files`` artifacts.

    Returns the paths only in A, only in B, and common to both (each sorted). When a run has
    no ``changed_files`` artifact the comparison does not fail -- it returns empty lists for
    that side and records a compact ``warning``.
    """
    has_a = any(a.type == ArtifactType.CHANGED_FILES.value for a in artifacts_a)
    has_b = any(a.type == ArtifactType.CHANGED_FILES.value for a in artifacts_b)
    paths_a = _changed_paths(artifacts_a)
    paths_b = _changed_paths(artifacts_b)

    missing = []
    if not has_a:
        missing.append("run A")
    if not has_b:
        missing.append("run B")
    warning = (
        f"No changed_files artifact for {' and '.join(missing)}; changed-file comparison may be incomplete."
        if missing
        else None
    )
    return ChangedFilesComparison(
        only_a=sorted(paths_a - paths_b),
        only_b=sorted(paths_b - paths_a),
        common=sorted(paths_a & paths_b),
        warning=warning,
    )


def compare_diff_stats(artifacts_a, artifacts_b) -> Tuple[str, str]:
    """Return each run's latest ``git_diff_stat`` raw text (capped), or empty strings."""
    stat_a = _latest_content(artifacts_a, ArtifactType.GIT_DIFF_STAT.value)[:_DIFF_STAT_CAP]
    stat_b = _latest_content(artifacts_b, ArtifactType.GIT_DIFF_STAT.value)[:_DIFF_STAT_CAP]
    return stat_a, stat_b


def compare_next_prompts(steps_a, steps_b, show_prompts: bool = False):
    """Compare the latest generated next prompt of each run.

    Returns ``(preview_a, preview_b, full_a, full_b)`` where the full text is ``None`` unless
    ``show_prompts`` is set. The latest next prompt is the last non-empty ``next_prompt`` in
    step order.
    """
    def latest(steps) -> str:
        text = ""
        for step in steps:
            if step.next_prompt:
                text = step.next_prompt
        return text

    next_a = latest(steps_a)
    next_b = latest(steps_b)
    return (
        _preview(next_a),
        _preview(next_b),
        next_a if show_prompts else None,
        next_b if show_prompts else None,
    )


def build_comparison_summary(
    meta: MetadataComparison, steps: StepComparison, changed: ChangedFilesComparison
) -> str:
    """A compact, deterministic one-line summary of the comparison."""
    a, b = meta.run_a, meta.run_b
    parts = [
        f"Run #{a.id} ({a.status}, {a.provider}, {steps.step_count_a} step(s)) vs "
        f"Run #{b.id} ({b.status}, {b.provider}, {steps.step_count_b} step(s))",
        "same provider" if meta.same_provider else "different providers",
        "same status" if meta.same_status else "different statuses",
        f"changed files: {len(changed.only_a)} only A, {len(changed.only_b)} only B, "
        f"{len(changed.common)} common",
        f"failed steps: {steps.failed_steps_a} A, {steps.failed_steps_b} B",
    ]
    return "; ".join(parts) + "."


def compare_runs(
    db_path: str,
    run_id_a: int,
    run_id_b: int,
    show_prompts: bool = False,
    show_artifacts: bool = True,
) -> RunComparison:
    """Load two runs from SQLite and compare their stored content.

    Raises ``CompareError("same_run", ...)`` if the ids are equal and
    ``CompareError("not_found", ...)`` if either run does not exist. Reads only stored
    database content -- no workspace files, no external tools.
    """
    if run_id_a == run_id_b:
        raise CompareError("same_run", f"cannot compare run {run_id_a} with itself")

    run_a = storage.get_run(db_path, run_id_a)
    if run_a is None:
        raise CompareError("not_found", f"run {run_id_a} not found")
    run_b = storage.get_run(db_path, run_id_b)
    if run_b is None:
        raise CompareError("not_found", f"run {run_id_b} not found")

    steps_a = storage.get_steps_for_run(db_path, run_id_a)
    steps_b = storage.get_steps_for_run(db_path, run_id_b)

    # Load only the small, type-filtered artifacts needed (never the large runner output).
    cf_a = storage.list_artifacts_for_run(db_path, run_id_a, ArtifactType.CHANGED_FILES.value)
    cf_b = storage.list_artifacts_for_run(db_path, run_id_b, ArtifactType.CHANGED_FILES.value)
    ds_a = storage.list_artifacts_for_run(db_path, run_id_a, ArtifactType.GIT_DIFF_STAT.value)
    ds_b = storage.list_artifacts_for_run(db_path, run_id_b, ArtifactType.GIT_DIFF_STAT.value)

    meta = compare_run_metadata(run_a, run_b, show_prompts=show_prompts)
    steps = compare_steps(steps_a, steps_b)
    changed = compare_changed_files(cf_a, cf_b)
    diff_stat_a, diff_stat_b = compare_diff_stats(ds_a, ds_b)
    np_a, np_b, np_full_a, np_full_b = compare_next_prompts(steps_a, steps_b, show_prompts=show_prompts)

    counts_a = storage.count_artifacts_by_type(db_path, run_id_a) if show_artifacts else {}
    counts_b = storage.count_artifacts_by_type(db_path, run_id_b) if show_artifacts else {}

    return RunComparison(
        run_a=meta.run_a,
        run_b=meta.run_b,
        same_provider=meta.same_provider,
        same_status=meta.same_status,
        steps=steps,
        changed_files=changed,
        diff_stat_a=diff_stat_a,
        diff_stat_b=diff_stat_b,
        latest_next_prompt_a=np_a,
        latest_next_prompt_b=np_b,
        latest_next_prompt_full_a=np_full_a,
        latest_next_prompt_full_b=np_full_b,
        artifact_counts_by_type_a=ArtifactCounts(counts=counts_a),
        artifact_counts_by_type_b=ArtifactCounts(counts=counts_b),
        summary=build_comparison_summary(meta, steps, changed),
    )
