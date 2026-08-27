from __future__ import annotations

import datetime as dt
from typing import Literal

from trading_agent.autonomous_memory_models import AutonomousMemoryRecord
from trading_agent.dashboard_models_v2 import TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_outbound_redaction import redact_outbound_text, require_safe_outbound_text
from trading_agent.kr_autonomous_outcome_models import KrAutonomousOutcomeMemory, KrLoopEngineerEvidenceBundle
from trading_agent.kr_loop_engineer_models import KrLoopCandidateSnapshot, KrLoopCandidateState

type _NodeKind = Literal["source_receipt", "reviewer_decision"]


def outcome_workspace_item(record: AutonomousMemoryRecord) -> WorkspaceItemV2:
    outcome = KrAutonomousOutcomeMemory.model_validate_json(record.summary)
    horizons = ",".join(f"{item.horizon.value}:{item.return_bps:+f}bps" for item in outcome.horizons) or "none"
    value = (
        f"virtual;state={outcome.execution_state.value};market={outcome.market_evidence_state.value};"
        f"phase={outcome.session_phase.value};horizons={horizons};{outcome.verification_state.value}"
    )
    return _workspace_item(
        f"kr-outcome-{record.memory_id[:24]}",
        f"KR 가상 결과 · {outcome.symbol}",
        value,
        record.recorded_at,
        f"trace.kr.task.{outcome.task_id[:24]}",
    )


def bundle_workspace_item(record: AutonomousMemoryRecord) -> WorkspaceItemV2:
    bundle = KrLoopEngineerEvidenceBundle.model_validate_json(record.summary)
    return _workspace_item(
        f"kr-loop-{record.memory_id[:24]}",
        "KR Loop Engineer 증거 묶음",
        f"failure={bundle.failure_code.value};samples={len(bundle.source_memory_ids)};code_mutation=false",
        record.recorded_at,
        f"trace.kr.task.{bundle.source_task_ids[0][:24]}",
    )


def loop_workspace_item(snapshot: KrLoopCandidateSnapshot) -> WorkspaceItemV2:
    value = (
        f"state={snapshot.state.value};shadow_sessions={len(snapshot.shadow_receipts)};"
        "paper_only=true;trading_authority=false"
    )
    return _workspace_item(
        f"kr-loop-state-{snapshot.snapshot_id[:24]}",
        f"KR Loop Engineer · {snapshot.state.value}",
        value,
        snapshot.updated_at,
        f"trace.kr.loop.{snapshot.snapshot_id[:24]}",
    )


def append_learning_trace(
    nodes: dict[str, TraceNodeV2],
    edges: dict[tuple[str, str], TraceEdgeV2],
    *,
    outcomes: tuple[AutonomousMemoryRecord, ...],
    bundles: tuple[AutonomousMemoryRecord, ...],
    loop_snapshots: tuple[KrLoopCandidateSnapshot, ...],
) -> None:
    for record in outcomes:
        outcome = KrAutonomousOutcomeMemory.model_validate_json(record.summary)
        root = _task_node(nodes, outcome.task_id, record.recorded_at)
        parent = (
            f"trace.kr.position.{outcome.position_event_id[:24]}"
            if outcome.position_event_id
            else f"trace.kr.decision.{outcome.trade_event_id[:24]}"
        )
        node = _node(
            f"trace.kr.outcome.{record.memory_id[:24]}",
            "reviewer_decision",
            outcome.execution_state.value,
            record.recorded_at,
            str(record.memory_id),
        )
        nodes[node.node_id] = node
        source = parent if parent in nodes else root
        edges[(source, node.node_id)] = TraceEdgeV2(
            from_node_id=source,
            to_node_id=node.node_id,
            kind="evaluated_in",
        )
    bundle_nodes: dict[str, str] = {}
    for record in bundles:
        bundle = KrLoopEngineerEvidenceBundle.model_validate_json(record.summary)
        root = _task_node(nodes, bundle.source_task_ids[0], record.recorded_at)
        node = _node(
            f"trace.kr.bundle.{record.memory_id[:24]}",
            "reviewer_decision",
            bundle.failure_code.value,
            record.recorded_at,
            str(record.memory_id),
        )
        nodes[node.node_id] = node
        bundle_nodes[bundle.bundle_id] = node.node_id
        edges[(root, node.node_id)] = TraceEdgeV2(
            from_node_id=root,
            to_node_id=node.node_id,
            kind="evaluated_in",
        )
        for memory_id in bundle.source_memory_ids:
            source = f"trace.kr.outcome.{memory_id[:24]}"
            if source in nodes:
                edges[(source, node.node_id)] = TraceEdgeV2(
                    from_node_id=source,
                    to_node_id=node.node_id,
                    kind="derived_from",
                )
    for snapshot in sorted(loop_snapshots, key=lambda item: (item.updated_at, item.snapshot_id)):
        node_id = f"trace.kr.loop.{snapshot.snapshot_id[:24]}"
        terminal = snapshot.state in {KrLoopCandidateState.REJECTED, KrLoopCandidateState.ROLLED_BACK}
        node = TraceNodeV2(
            node_id=node_id,
            kind="lifecycle_decision"
            if terminal or snapshot.state is KrLoopCandidateState.PROMOTED
            else "code_revision",
            label=redact_outbound_text(f"KR Loop Engineer · {snapshot.state.value}", max_chars=100),
            observed_at=snapshot.updated_at,
            safe_ref=snapshot.snapshot_id,
            state="failed" if terminal else "accepted",
            source_namespace="dashboard.kr_loop_engineer",
        )
        nodes[node_id] = node
        previous = (
            None if snapshot.previous_snapshot_id is None else f"trace.kr.loop.{snapshot.previous_snapshot_id[:24]}"
        )
        parent = previous if previous in nodes else bundle_nodes.get(snapshot.bundle_id)
        if parent is not None:
            kind = "deployed_as" if snapshot.state is KrLoopCandidateState.PROMOTED else "derived_from"
            edges[(parent, node_id)] = TraceEdgeV2(from_node_id=parent, to_node_id=node_id, kind=kind)


def _workspace_item(
    item_id: str,
    label: str,
    value: str,
    observed_at: dt.datetime,
    trace_id: str,
) -> WorkspaceItemV2:
    safe_label = redact_outbound_text(label, max_chars=80)
    safe_value = redact_outbound_text(value, max_chars=160)
    require_safe_outbound_text(f"{safe_label} {safe_value}")
    return WorkspaceItemV2(
        item_id=item_id,
        kind="research",
        label=safe_label,
        state="populated",
        value=safe_value,
        observed_at=observed_at,
        trace_id=trace_id,
    )


def _task_node(nodes: dict[str, TraceNodeV2], task_id: str, observed_at: dt.datetime) -> str:
    node_id = f"trace.kr.task.{task_id[:24]}"
    nodes.setdefault(node_id, _node(node_id, "source_receipt", "durable KR research task", observed_at, task_id))
    return node_id


def _node(node_id: str, kind: _NodeKind, label: str, observed_at: dt.datetime, safe_ref: str) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label=redact_outbound_text(f"KR autonomous · {label}", max_chars=100),
        observed_at=observed_at,
        safe_ref=safe_ref,
        state="accepted",
        source_namespace="dashboard.kr_autonomous",
    )


__all__ = (
    "append_learning_trace",
    "bundle_workspace_item",
    "loop_workspace_item",
    "outcome_workspace_item",
)
