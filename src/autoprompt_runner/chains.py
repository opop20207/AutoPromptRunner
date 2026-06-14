"""Prompt chain history for a run.

Reconstructs how a run evolved -- root prompt, each step's prompt and generated next
prompt, the approval decision, the provider result, and the artifacts captured -- into a
linear chain of nodes (one per step, ordered by loop index then step id).

Like ``search`` and ``compare``, it reads only already-stored database content: runs,
steps, approvals, and artifact *counts*/changed-file *paths*. It never reads workspace
files from disk, never calls an external tool, does no semantic prompt analysis, and never
surfaces secret-file contents. Previews are compact; missing artifacts or approvals never
fail chain creation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import storage
from .artifacts import ArtifactType

_PROMPT_PREVIEW = 200
_OUTPUT_PREVIEW = 200
_CHANGED_FILES_CAP = 20


class ChainError(Exception):
    """Raised when a chain cannot be built. ``kind`` is ``"not_found"`` (run is missing)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class ArtifactCounts:
    counts: Dict[str, int] = field(default_factory=dict)


@dataclass
class ChainNode:
    node_id: str
    run_id: int
    step_id: int
    loop_index: int
    prompt: Optional[str]
    prompt_preview: str
    next_prompt: Optional[str]
    next_prompt_preview: str
    status: str
    exit_code: Optional[int]
    provider: str
    started_at: Optional[str]
    finished_at: Optional[str]
    approval_status: Optional[str]
    artifact_counts_by_type: ArtifactCounts
    changed_files_preview: List[str]
    stderr_preview: str
    stdout_preview: str


@dataclass
class ChainSummary:
    run_id: int
    root_prompt: str
    provider: str
    run_status: str
    step_count: int
    approval_count: int
    pending_approval: bool
    failed_step_count: int
    total_artifact_count: int
    chain_nodes: List[ChainNode]


def _preview(text: Optional[str], limit: int = _PROMPT_PREVIEW) -> str:
    """Collapse whitespace and truncate to a compact, single-line preview."""
    norm = " ".join((text or "").split())
    return norm if len(norm) <= limit else norm[:limit] + "…"


def _is_failed(status: Optional[str], exit_code: Optional[int]) -> bool:
    if (status or "").upper() == "FAILED":
        return True
    return exit_code is not None and exit_code != 0


def build_chain_node(
    step,
    provider: str,
    approval_status: Optional[str],
    artifact_counts: Optional[Dict[str, int]],
    changed_files: Optional[List[str]],
    full_prompts: bool = False,
) -> ChainNode:
    """Build one chain node from a stored step plus its approval/artifact context."""
    return ChainNode(
        node_id=f"{step.run_id}:{step.id}",
        run_id=step.run_id,
        step_id=step.id,
        loop_index=step.loop_index,
        prompt=step.prompt if full_prompts else None,
        prompt_preview=_preview(step.prompt),
        next_prompt=step.next_prompt if full_prompts else None,
        next_prompt_preview=_preview(step.next_prompt),
        status=step.status,
        exit_code=step.exit_code,
        provider=provider,
        started_at=step.started_at,
        finished_at=step.finished_at,
        approval_status=approval_status,
        artifact_counts_by_type=ArtifactCounts(counts=dict(artifact_counts or {})),
        changed_files_preview=list(changed_files or []),
        stderr_preview=_preview(step.stderr, _OUTPUT_PREVIEW),
        stdout_preview=_preview(step.stdout, _OUTPUT_PREVIEW),
    )


def get_latest_chain_node(nodes: List[ChainNode]) -> Optional[ChainNode]:
    """Return the last node (highest loop index), or ``None`` for an empty chain."""
    return nodes[-1] if nodes else None


def get_failed_chain_nodes(nodes: List[ChainNode]) -> List[ChainNode]:
    """Return the nodes whose step failed (status FAILED or a non-zero exit code)."""
    return [n for n in nodes if _is_failed(n.status, n.exit_code)]


def get_pending_approval_node(nodes: List[ChainNode]) -> Optional[ChainNode]:
    """Return the first node still waiting on a PENDING approval, or ``None``."""
    for node in nodes:
        if (node.approval_status or "").upper() == "PENDING":
            return node
    return None


def summarize_chain(run, nodes: List[ChainNode], approval_count: int, total_artifact_count: int) -> ChainSummary:
    """Assemble the chain summary from the run and its (full, unfiltered) node list."""
    return ChainSummary(
        run_id=run.id,
        root_prompt=run.root_prompt,
        provider=run.provider,
        run_status=run.status,
        step_count=len(nodes),
        approval_count=approval_count,
        pending_approval=get_pending_approval_node(nodes) is not None,
        failed_step_count=len(get_failed_chain_nodes(nodes)),
        total_artifact_count=total_artifact_count,
        chain_nodes=nodes,
    )


def _changed_files_by_step(db_path: str, run_id: int) -> Dict[Optional[int], List[str]]:
    """Map ``step_id -> changed file paths`` from ``changed_files`` artifacts (capped, no disk)."""
    by_step: Dict[Optional[int], List[str]] = {}
    for artifact in storage.list_artifacts_for_run(db_path, run_id, ArtifactType.CHANGED_FILES.value):
        paths = by_step.setdefault(artifact.step_id, [])
        for line in (artifact.content or "").splitlines():
            path = line.strip()
            if path and len(paths) < _CHANGED_FILES_CAP:
                paths.append(path)
    return by_step


def build_prompt_chain(
    db_path: str,
    run_id: int,
    full_prompts: bool = False,
    include_artifacts: bool = True,
    errors_only: bool = False,
) -> ChainSummary:
    """Build the prompt chain for ``run_id`` from stored run/step/approval/artifact data.

    Raises ``ChainError("not_found", ...)`` if the run does not exist. Nodes are ordered by
    loop index then step id. The summary counts always reflect the full run; ``errors_only``
    filters only the returned ``chain_nodes`` to failed nodes.
    """
    run = storage.get_run(db_path, run_id)
    if run is None:
        raise ChainError("not_found", f"run {run_id} not found")

    steps = storage.get_steps_for_run(db_path, run_id)  # already ordered by loop_index, id
    approvals = storage.list_approvals_for_run(db_path, run_id)
    # Latest approval status per step (ascending id means the last write wins).
    approval_by_step: Dict[int, str] = {a.step_id: a.status for a in approvals}

    total_artifact_count = sum(storage.count_artifacts_by_type(db_path, run_id).values())
    counts_by_step: Dict[Optional[int], Dict[str, int]] = {}
    changed_by_step: Dict[Optional[int], List[str]] = {}
    if include_artifacts:
        counts_by_step = storage.count_artifacts_by_type_and_step(db_path, run_id)
        changed_by_step = _changed_files_by_step(db_path, run_id)

    nodes = [
        build_chain_node(
            step,
            run.provider,
            approval_by_step.get(step.id),
            counts_by_step.get(step.id),
            changed_by_step.get(step.id),
            full_prompts=full_prompts,
        )
        for step in steps
    ]

    summary = summarize_chain(run, nodes, len(approvals), total_artifact_count)
    if errors_only:
        summary.chain_nodes = get_failed_chain_nodes(nodes)
    return summary
