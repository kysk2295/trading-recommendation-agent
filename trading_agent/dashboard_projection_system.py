from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Final

from trading_agent.dashboard_models_v2 import SourceStateName, WorkspaceItemV2
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.dashboard_projection_system_control import project_autonomous_control
from trading_agent.dashboard_system_current_authority import SystemAuthorityVerifier
from trading_agent.dashboard_system_evidence import project_milestone_evidence
from trading_agent.dashboard_system_operations import project_system_operations

_STATE_PRIORITY: Final[dict[SourceStateName, int]] = {
    "loading": 0,
    "empty": 1,
    "populated": 2,
    "stale": 3,
    "unavailable": 4,
    "blocked": 5,
    "error": 6,
    "corrupt": 7,
}


def project_system(
    outputs: Path,
    *,
    now: dt.datetime,
    authority_verifier: SystemAuthorityVerifier | None = None,
) -> WorkspaceProjection:
    milestones = project_milestone_evidence(outputs, now=now)
    operations = project_system_operations(
        outputs,
        now=now,
        authority_verifier=authority_verifier,
    )
    autonomous = project_autonomous_control(outputs, now=now)
    projections = (milestones, operations, autonomous)
    worst = max(projections, key=lambda item: _STATE_PRIORITY[item.workspace.state])
    operation_items = _operation_representatives(operations)
    items = milestones.workspace.items + operation_items + autonomous.workspace.items
    items = items[:24]
    selected_traces = {item.trace_id for item in items} | {
        projection.workspace.trace_id for projection in projections
    }
    nodes = tuple(
        node
        for projection in projections
        for node in projection.nodes
        if node.node_id in selected_traces
        or any(
            edge.from_node_id in selected_traces and edge.to_node_id == node.node_id
            for edge in projection.edges
        )
    )
    node_ids = {node.node_id for node in nodes}
    edges = tuple(
        edge
        for projection in projections
        for edge in projection.edges
        if edge.from_node_id in node_ids and edge.to_node_id in node_ids
    )
    total = sum(projection.workspace.total_count for projection in projections)
    observed = tuple(
        projection.workspace.observed_at
        for projection in projections
        if projection.workspace.observed_at is not None
    )
    return WorkspaceProjection(
        milestones.workspace.model_copy(
            update={
                "state": worst.workspace.state,
                "observed_at": max(observed) if observed else None,
                "freshness": worst.workspace.freshness.model_copy(update={"as_of": now}),
                "blocker_code": worst.workspace.blocker_code,
                "summary": "M0-M10, runtime, release, relay, and autonomous evidence",
                "total_count": total,
                "projected_count": len(items),
                "truncated": total > len(items),
                "trace_id": worst.workspace.trace_id,
                "items": items,
            }
        ),
        nodes,
        edges,
    )


def _operation_representatives(
    operations: WorkspaceProjection,
) -> tuple[WorkspaceItemV2, ...]:
    selected: list[WorkspaceItemV2] = []
    for category in ("launchd", "stage", "railway", "relay"):
        candidates = tuple(
            item
            for item in operations.workspace.items
            if item.item_id.startswith(f"system.operation.{category}.")
        )
        if candidates:
            selected.append(
                max(
                    candidates,
                    key=lambda item: (
                        item.observed_at or dt.datetime.min.replace(tzinfo=dt.UTC)
                    ),
                )
            )
    return tuple(selected)


__all__ = ("project_system",)
