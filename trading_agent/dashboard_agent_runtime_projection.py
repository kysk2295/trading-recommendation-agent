from __future__ import annotations

import datetime as dt
import hashlib

from trading_agent.dashboard_agent_cycle_runtime import (
    AgentRuntimeObservation,
    AgentRuntimeState,
)
from trading_agent.dashboard_agent_family import AGENT_FAMILY_REGISTRY, AgentFamilyId
from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    PublicAgentViewV2,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_common import WorkspaceProjection


def build_agent_runtime_projection(
    observations: tuple[AgentRuntimeObservation, ...],
    *,
    now: dt.datetime,
    source_namespace: str,
) -> tuple[WorkspaceProjection, tuple[PublicAgentViewV2, ...]]:
    by_family = {item.family: item for item in observations}
    agents: list[PublicAgentViewV2] = []
    items: list[WorkspaceItemV2] = []
    nodes: list[TraceNodeV2] = []
    edges: list[TraceEdgeV2] = []
    observed = tuple(item.observed_at for item in observations)
    for family in AGENT_FAMILY_REGISTRY:
        observation = by_family.get(family.family_id)
        state = "unavailable" if observation is None else observation.state
        trace_id = f"trace.command_center.agent.{family.family_id}.source"
        terminal_id = f"trace.command_center.agent.{family.family_id}.runtime"
        timestamp = None if observation is None else observation.observed_at
        agents.append(
            PublicAgentViewV2(
                agent_id=family.family_id,
                label=family.family_id.replace("_", " ").title(),
                role=family.role,
                capabilities=family.capabilities,
                runtime_state=state,
                trace_id=trace_id,
            )
        )
        items.append(_workspace_item(family.family_id, state, timestamp, trace_id))
        safe_ref = hashlib.sha256(f"{family.family_id}:{state}".encode()).hexdigest()
        nodes.extend(_trace_nodes(family.family_id, state, timestamp or now, safe_ref, source_namespace))
        edges.append(
            TraceEdgeV2(
                from_node_id=trace_id,
                to_node_id=terminal_id,
                kind="blocked_by" if state in {"failed", "unavailable"} else "executed_as",
            )
        )
    missing = len(observations) < 6 or any(item.state == "unavailable" for item in observations)
    workspace_state = "unavailable" if not observations else "blocked" if missing else "populated"
    root_nodes, root_edge = _root_trace(workspace_state, max(observed, default=now), source_namespace)
    nodes.extend(root_nodes)
    edges.append(root_edge)
    workspace = SourceStateV2(
        state=workspace_state,
        observed_at=max(observed, default=None),
        freshness=FreshnessV2(
            policy_id="agent-cycle-runtime-v1",
            age_seconds=None if not observed else max(0, int((now - max(observed)).total_seconds())),
            as_of=now,
        ),
        blocker_code=(
            "agent_runtime_missing"
            if workspace_state == "unavailable"
            else "agent_channel_missing"
            if workspace_state == "blocked"
            else None
        ),
        summary="Exact six-family persistent research runtime",
        total_count=len(agents),
        projected_count=len(items),
        truncated=False,
        trace_id="trace.command_center.runtime",
        items=tuple(items),
    )
    return WorkspaceProjection(workspace=workspace, nodes=tuple(nodes), edges=tuple(edges)), tuple(agents)


def _workspace_item(
    family: AgentFamilyId,
    state: AgentRuntimeState,
    observed_at: dt.datetime | None,
    trace_id: str,
) -> WorkspaceItemV2:
    return WorkspaceItemV2(
        item_id=f"agent.{family}",
        kind="system",
        label=family.replace("_", " ").title(),
        state="unavailable" if state == "unavailable" else "error" if state == "failed" else "populated",
        value=state,
        observed_at=observed_at,
        trace_id=trace_id,
    )


def _trace_nodes(
    family: AgentFamilyId,
    state: AgentRuntimeState,
    observed_at: dt.datetime,
    safe_ref: str,
    namespace: str,
) -> tuple[TraceNodeV2, TraceNodeV2]:
    unavailable = state in {"failed", "unavailable"}
    return (
        TraceNodeV2(
            node_id=f"trace.command_center.agent.{family}.source",
            kind="source_receipt",
            label=f"{family} runtime authority",
            observed_at=observed_at,
            safe_ref=safe_ref,
            state="unavailable" if unavailable else "accepted",
            source_namespace=namespace,
        ),
        TraceNodeV2(
            node_id=f"trace.command_center.agent.{family}.runtime",
            kind="blocker_terminal" if unavailable else "process_receipt",
            label=f"{family} runtime readiness",
            observed_at=observed_at,
            safe_ref=safe_ref,
            state="blocked" if unavailable else "accepted",
            source_namespace=namespace,
        ),
    )


def _root_trace(
    workspace_state: str,
    observed_at: dt.datetime,
    namespace: str,
) -> tuple[tuple[TraceNodeV2, TraceNodeV2], TraceEdgeV2]:
    root_id = "trace.command_center.runtime"
    terminal_id = "trace.command_center.runtime.terminal"
    safe_ref = hashlib.sha256(b"command-center-agent-runtime-v2").hexdigest()
    unavailable = workspace_state != "populated"
    nodes = (
        TraceNodeV2(
            node_id=root_id,
            kind="source_receipt",
            label="Agent runtime authority",
            observed_at=observed_at,
            safe_ref=safe_ref,
            state="unavailable" if unavailable else "accepted",
            source_namespace=namespace,
        ),
        TraceNodeV2(
            node_id=terminal_id,
            kind="blocker_terminal" if unavailable else "process_receipt",
            label="Agent runtime readiness",
            observed_at=observed_at,
            safe_ref=safe_ref,
            state="blocked" if unavailable else "accepted",
            source_namespace=namespace,
        ),
    )
    edge = TraceEdgeV2(
        from_node_id=root_id,
        to_node_id=terminal_id,
        kind="blocked_by" if unavailable else "executed_as",
    )
    return nodes, edge


__all__ = ("build_agent_runtime_projection",)
