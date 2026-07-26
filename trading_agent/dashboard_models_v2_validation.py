from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from trading_agent.dashboard_models_v2 import (
        DashboardSnapshotV2,
        TraceEdgeV2,
        TraceNodeV2,
        WorkspacesV2,
    )

_DECISION_TERMINALS = {
    "reviewer_decision",
    "lifecycle_decision",
    "paper_receipt",
    "process_receipt",
    "deployment_receipt",
    "blocker_terminal",
}
_TERMINALS_BY_WORKSPACE = {
    "command_center": {"process_receipt", "blocker_terminal"},
    "overview": {
        "source_receipt",
        "reviewer_decision",
        "lifecycle_decision",
        "paper_receipt",
        "process_receipt",
        "blocker_terminal",
    },
    "markets": {"source_receipt", "reviewer_decision", "blocker_terminal"},
    "data_sources": {"source_receipt", "reviewer_decision", "blocker_terminal"},
    "research": {"reviewer_decision", "blocker_terminal"},
    "strategies": {"reviewer_decision", "lifecycle_decision", "blocker_terminal"},
    "derivatives": {"source_receipt", "reviewer_decision", "blocker_terminal"},
    "paper": {"paper_receipt", "blocker_terminal"},
    "system": {
        "reviewer_decision",
        "process_receipt",
        "deployment_receipt",
        "blocker_terminal",
    },
}


def validate_snapshot(snapshot: DashboardSnapshotV2) -> None:
    if len(snapshot.model_dump_json().encode()) > 256 * 1024:
        raise InvalidSnapshotMetadataError(reason="snapshot_too_large")
    current_ceiling = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5)
    if snapshot.generated_at > current_ceiling:
        raise InvalidSnapshotMetadataError(reason="generated_at_too_far_future")
    observation_ceiling = min(
        snapshot.generated_at + dt.timedelta(minutes=5),
        current_ceiling,
    )
    _validate_observations(snapshot, observation_ceiling)
    nodes = {node.node_id: node for node in snapshot.traces.nodes}
    if len(nodes) != len(snapshot.traces.nodes):
        raise InvalidSnapshotMetadataError(reason="duplicate_trace_node")
    adjacency = {node_id: set[str]() for node_id in nodes}
    for edge in snapshot.traces.edges:
        if edge.from_node_id not in nodes or edge.to_node_id not in nodes:
            raise InvalidSnapshotMetadataError(reason="dangling_trace_edge")
        if edge.to_node_id in adjacency[edge.from_node_id]:
            raise InvalidSnapshotMetadataError(reason="duplicate_trace_edge")
        adjacency[edge.from_node_id].add(edge.to_node_id)
    for references, terminals in _reference_groups(snapshot.workspaces):
        for reference in references:
            if reference not in nodes:
                raise InvalidSnapshotMetadataError(reason="dangling_trace_reference")
            kinds = {nodes[node_id].kind for node_id in _reachable(reference, adjacency)}
            if "source_receipt" not in kinds:
                raise InvalidSnapshotMetadataError(reason="trace_source_missing")
            if not kinds & terminals:
                reason = "trace_terminal_wrong_domain" if kinds & _DECISION_TERMINALS else "trace_terminal_missing"
                raise InvalidSnapshotMetadataError(reason=reason)
    _reject_cycle(nodes, snapshot.traces.edges)


def _validate_observations(
    snapshot: DashboardSnapshotV2,
    ceiling: dt.datetime,
) -> None:
    timestamps: list[dt.datetime | None] = [node.observed_at for node in snapshot.traces.nodes]
    for _, workspace in snapshot.workspaces:
        if workspace.state == "loading":
            raise InvalidSnapshotMetadataError(reason="publisher_loading_state")
        if workspace.state != "unavailable" and workspace.observed_at is None:
            raise InvalidSnapshotMetadataError(reason="observed_at_required")
        timestamps.extend([workspace.observed_at, workspace.freshness.as_of])
        for item in workspace.items:
            if item.state == "loading":
                raise InvalidSnapshotMetadataError(reason="publisher_loading_state")
            if item.state != "unavailable" and item.observed_at is None:
                raise InvalidSnapshotMetadataError(reason="observed_at_required")
            timestamps.append(item.observed_at)
    for capability in snapshot.workspaces.data_sources.capabilities:
        if capability.state == "loading":
            raise InvalidSnapshotMetadataError(reason="publisher_loading_state")
        if capability.state != "unavailable" and capability.observed_at is None:
            raise InvalidSnapshotMetadataError(reason="observed_at_required")
        timestamps.append(capability.observed_at)
    if any(timestamp is not None and timestamp > ceiling for timestamp in timestamps):
        raise InvalidSnapshotMetadataError(reason="observation_too_far_future")


def _reference_groups(
    workspaces: WorkspacesV2,
) -> tuple[tuple[set[str], set[str]], ...]:
    groups: list[tuple[set[str], set[str]]] = []
    for name, workspace in workspaces:
        groups.append(({workspace.trace_id}, _terminals_for_state(workspace.state, name)))
        groups.extend(({item.trace_id}, _terminals_for_state(item.state, name)) for item in workspace.items)
        if name == "command_center":
            groups.append(
                (
                    {agent.trace_id for agent in workspaces.command_center.agents},
                    _TERMINALS_BY_WORKSPACE[name],
                )
            )
        elif name == "data_sources":
            groups.extend(
                (
                    {capability.trace_id},
                    _terminals_for_state(capability.state, name),
                )
                for capability in workspaces.data_sources.capabilities
            )
    return tuple(groups)


def _terminals_for_state(state: str, workspace_name: str) -> set[str]:
    if state in {"error", "blocked", "unavailable", "corrupt"}:
        return {"blocker_terminal"}
    return _TERMINALS_BY_WORKSPACE[workspace_name]


def _reachable(start: str, adjacency: dict[str, set[str]]) -> set[str]:
    reached = {start}
    queue = [start]
    for node_id in queue:
        for next_id in adjacency[node_id]:
            if next_id not in reached:
                reached.add(next_id)
                queue.append(next_id)
    return reached


def _reject_cycle(nodes: dict[str, TraceNodeV2], edges: tuple[TraceEdgeV2, ...]) -> None:
    remaining = set(nodes)
    while remaining:
        roots = {
            node_id
            for node_id in remaining
            if all(edge.to_node_id != node_id or edge.from_node_id not in remaining for edge in edges)
        }
        if not roots:
            raise InvalidSnapshotMetadataError(reason="cyclic_trace_graph")
        remaining -= roots


@dataclass(frozen=True, slots=True)
class InvalidSnapshotMetadataError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason
