from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Literal

from trading_agent.dashboard_models_v2 import FreshnessV2, SourceStateV2, TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_projection_common import WorkspaceProjection, blocked_projection
from trading_agent.dashboard_projection_experiment_chains import ExperimentChain, read_experiment_chains
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.lane_review_store import (
    InvalidLaneReviewSourceError,
    LaneReviewReader,
    UnsupportedLaneReviewSchemaError,
)

Workspace = Literal["research", "strategies"]
TraceNodeKind = Literal[
    "source_receipt",
    "hypothesis",
    "dataset",
    "code_revision",
    "trial",
    "reviewer_decision",
    "lifecycle_decision",
    "blocker_terminal",
]
TraceEdgeKind = Literal["derived_from", "bound_to", "evaluated_in", "reviewed_by", "decided_by", "blocked_by"]


def project_research(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    return _project("research", outputs, now)


def project_strategies(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    return _project("strategies", outputs, now)


def _project(workspace: Workspace, outputs: Path, now: dt.datetime) -> WorkspaceProjection:
    ledger = ExperimentLedgerReader(outputs / "experiment_control" / "experiment_ledger.sqlite3")
    if not ledger.is_initialized():
        return blocked_projection(workspace, now=now, state="unavailable", blocker_code=_missing_catalog(workspace))
    reviews = LaneReviewReader(outputs / "lane_control" / "lane_review.sqlite3")
    try:
        chains, allocation_available = read_experiment_chains(ledger, reviews, strategies=workspace == "strategies")
    except (
        InvalidExperimentLedgerSourceError,
        InvalidLaneReviewSourceError,
        UnsupportedExperimentLedgerSchemaError,
        UnsupportedLaneReviewSchemaError,
    ):
        return blocked_projection(workspace, now=now, state="corrupt", blocker_code=_invalid_lineage(workspace))
    if any(chain.observed_at > now + dt.timedelta(minutes=5) for chain in chains):
        return blocked_projection(workspace, now=now, state="corrupt", blocker_code="research_future_observation")
    if not chains:
        return blocked_projection(workspace, now=now, state="blocked", blocker_code="source_card_missing")
    return _projection(workspace, chains, allocation_available, now)


def _projection(
    workspace: Workspace,
    chains: tuple[ExperimentChain, ...],
    allocation_available: bool,
    now: dt.datetime,
) -> WorkspaceProjection:
    root_id = f"trace.{workspace}.ledger"
    accepted = tuple(chain for chain in chains if chain.blocker is None)
    root_ref = _sha(f"{workspace}:ledger")
    nodes = [_node(root_id, "source_receipt", now, root_ref, workspace, "experiment ledger receipt")]
    edges: list[TraceEdgeV2] = []
    items: list[WorkspaceItemV2] = []
    terminals: list[str] = []
    for index, chain in enumerate(chains[:24]):
        trace_id, terminal = _append_chain(nodes, edges, workspace, index, chain)
        terminals.append(terminal)
        items.append(
            WorkspaceItemV2(
                item_id=f"{workspace}.chain.{index}",
                kind="research" if workspace == "research" else "strategy",
                label=chain.label[:80],
                state="populated" if chain.blocker is None else "blocked",
                value=f"{chain.value} · code:{chain.code_version}"[:160],
                observed_at=chain.observed_at,
                trace_id=trace_id,
            )
        )
    if workspace == "strategies":
        _append_allocation_authority(nodes, edges, items, now, allocation_available)
    complete = len(accepted) == len(chains)
    blocker = next((chain.blocker for chain in chains if chain.blocker is not None), None)
    root_terminal = terminals[0] if complete else f"{root_id}.blocker"
    if not complete:
        nodes.append(_node(root_terminal, "blocker_terminal", now, root_ref, workspace, blocker or "authority missing"))
    edges.append(_edge(root_id, root_terminal, _root_edge(workspace, complete)))
    state = "populated" if complete else "blocked"
    allocation = "available" if allocation_available else "locked"
    return WorkspaceProjection(
        SourceStateV2(
            state=state,
            observed_at=max(chain.observed_at for chain in chains),
            freshness=FreshnessV2(policy_id=f"{workspace}-ledger-v2", age_seconds=0, as_of=now),
            blocker_code=None if complete else blocker,
            summary=f"{workspace} ledger projected; allocation manager {allocation}",
            total_count=len(chains) + (1 if workspace == "strategies" else 0),
            projected_count=len(items),
            truncated=len(chains) > len(items),
            trace_id=root_id,
            items=tuple(items),
        ),
        tuple(nodes),
        tuple(edges),
    )


def _append_allocation_authority(
    nodes: list[TraceNodeV2],
    edges: list[TraceEdgeV2],
    items: list[WorkspaceItemV2],
    now: dt.datetime,
    allocation_available: bool,
) -> None:
    trace_id = "trace.strategies.allocation"
    terminal_id = f"{trace_id}.terminal"
    terminal_kind: Literal["lifecycle_decision", "blocker_terminal"] = (
        "lifecycle_decision" if allocation_available else "blocker_terminal"
    )
    terminal_label = "two independent champion receipts" if allocation_available else "allocation_authority_missing"
    nodes.append(_node(trace_id, "source_receipt", now, _sha(trace_id), "strategies", "allocation authority receipt"))
    nodes.append(_node(terminal_id, terminal_kind, now, _sha(terminal_label), "strategies", terminal_label))
    edge_kind: Literal["decided_by", "blocked_by"] = "decided_by" if allocation_available else "blocked_by"
    edges.append(_edge(trace_id, terminal_id, edge_kind))
    items.append(
        WorkspaceItemV2(
            item_id="strategies.allocation_authority",
            kind="system",
            label="Allocation Manager",
            state="populated" if allocation_available else "blocked",
            value="Authority present · read-only; no mutation control"
            if allocation_available
            else "Locked · requires two independent champion receipts",
            observed_at=now,
            trace_id=trace_id,
        )
    )


def _append_chain(
    nodes: list[TraceNodeV2], edges: list[TraceEdgeV2], workspace: Workspace, index: int, chain: ExperimentChain
) -> tuple[str, str]:
    source_id = f"trace.{workspace}.chain.{index}.source"
    nodes.append(
        _node(
            source_id,
            "source_receipt",
            chain.observed_at,
            chain.source_ref or _sha(source_id),
            workspace,
            "research source",
        )
    )
    current = source_id
    reviewer_id: str | None = None
    stages: tuple[tuple[str, TraceNodeKind, str | None, str, TraceEdgeKind], ...] = (
        ("hypothesis", "hypothesis", chain.hypothesis_ref, "hypothesis", "derived_from"),
        ("dataset", "dataset", chain.dataset_sha, "dataset binding", "bound_to"),
        ("code", "code_revision", chain.code_ref, "code revision", "bound_to"),
        ("trial", "trial", chain.trial_ref, "trial", "evaluated_in"),
        ("terminal", "trial", chain.terminal_ref, "trial terminal", "evaluated_in"),
        ("reviewer", "reviewer_decision", chain.reviewer_ref, "independent reviewer", "reviewed_by"),
        ("lifecycle", "lifecycle_decision", chain.lifecycle_ref, "lifecycle decision", "decided_by"),
    )
    for suffix, kind, reference, label, edge_kind in stages:
        if reference is None:
            return _append_blocker(nodes, edges, workspace, current, chain, source_id)
        node_id = f"{source_id}.{suffix}"
        nodes.append(_node(node_id, kind, chain.observed_at, reference, workspace, label))
        edges.append(_edge(current, node_id, edge_kind))
        current = node_id
        if suffix == "reviewer":
            reviewer_id = node_id
    if workspace == "research" and reviewer_id is not None:
        return source_id, reviewer_id
    return source_id, current


def _append_blocker(
    nodes: list[TraceNodeV2],
    edges: list[TraceEdgeV2],
    workspace: Workspace,
    current: str,
    chain: ExperimentChain,
    source_id: str,
) -> tuple[str, str]:
    blocker = f"{source_id}.blocker"
    nodes.append(
        _node(
            blocker,
            "blocker_terminal",
            chain.observed_at,
            _sha(chain.blocker or blocker),
            workspace,
            chain.blocker or "authority missing",
        )
    )
    edges.append(_edge(current, blocker, "blocked_by"))
    return source_id, blocker


def _node(
    node_id: str,
    kind: TraceNodeKind,
    observed_at: dt.datetime,
    safe_ref: str,
    workspace: Workspace,
    label: str,
) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label=label,
        observed_at=observed_at,
        safe_ref=safe_ref,
        state="blocked" if kind == "blocker_terminal" else "accepted",
        source_namespace=f"experiment_ledger.{workspace}",
    )


def _edge(
    from_node_id: str,
    to_node_id: str,
    kind: TraceEdgeKind,
) -> TraceEdgeV2:
    return TraceEdgeV2(from_node_id=from_node_id, to_node_id=to_node_id, kind=kind)


def _root_edge(workspace: Workspace, accepted: bool) -> Literal["reviewed_by", "decided_by", "blocked_by"]:
    if not accepted:
        return "blocked_by"
    return "reviewed_by" if workspace == "research" else "decided_by"


def _missing_catalog(workspace: Workspace) -> str:
    return "research_catalog_missing" if workspace == "research" else "lane_registry_missing"


def _invalid_lineage(workspace: Workspace) -> str:
    return "research_lineage_invalid" if workspace == "research" else "trial_binding_invalid"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
