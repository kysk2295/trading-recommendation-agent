from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal, assert_never

from trading_agent.autonomous_memory_models import AutonomousMemoryRecord
from trading_agent.autonomous_task_models import AutonomousResearchTask
from trading_agent.dashboard_kr_autonomous_learning_render import (
    append_learning_trace,
    bundle_workspace_item,
    loop_workspace_item,
    outcome_workspace_item,
)
from trading_agent.dashboard_models_v2 import TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_outbound_redaction import redact_outbound_text, require_safe_outbound_text
from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousNoTrade,
    KrAutonomousRejected,
    KrAutonomousTradeEvent,
    KrTradeRecommendation,
)
from trading_agent.kr_loop_engineer_models import KrLoopCandidateSnapshot
from trading_agent.kr_social_signal_models import KrSocialSignal
from trading_agent.kr_virtual_position_models import KrVirtualPositionEvent

type _ItemKind = Literal["research", "day_recommendation", "paper"]
type _NodeKind = Literal[
    "source_receipt",
    "reviewer_decision",
    "paper_receipt",
]


@dataclass(frozen=True, slots=True)
class RenderedKrAutonomousOperator:
    markets: tuple[WorkspaceItemV2, ...]
    research: tuple[WorkspaceItemV2, ...]
    paper: tuple[WorkspaceItemV2, ...]
    nodes: tuple[TraceNodeV2, ...]
    edges: tuple[TraceEdgeV2, ...]


def render_kr_autonomous_operator(
    *,
    tasks: tuple[AutonomousResearchTask, ...],
    trades: tuple[KrAutonomousTradeEvent, ...],
    positions: tuple[KrVirtualPositionEvent, ...],
    outcomes: tuple[AutonomousMemoryRecord, ...],
    bundles: tuple[AutonomousMemoryRecord, ...],
    loop_snapshots: tuple[KrLoopCandidateSnapshot, ...],
    signals: tuple[KrSocialSignal, ...],
    now: dt.datetime,
) -> RenderedKrAutonomousOperator:
    del now
    signal_by_task = {signal.task_id: signal for signal in signals}
    markets = tuple(_trade_item(event, signal_by_task.get(event.task_id)) for event in trades)
    paper = tuple(_position_item(event) for event in positions)
    research = (
        *(_task_item(task) for task in tasks),
        *(outcome_workspace_item(record) for record in outcomes),
        *(bundle_workspace_item(record) for record in bundles),
        *(loop_workspace_item(snapshot) for snapshot in loop_snapshots),
    )
    nodes, edges = _trace(tasks, trades, positions, outcomes, bundles, loop_snapshots)
    return RenderedKrAutonomousOperator(markets, research, paper, nodes, edges)


def _task_item(task: AutonomousResearchTask) -> WorkspaceItemV2:
    wake = task.next_wake_at.isoformat() if task.next_wake_at is not None else task.next_wake_event or "none"
    return _item(
        f"kr-task-{task.task_id[:24]}",
        f"KR 자율 연구 · {task.owner_role.value}",
        "research",
        f"state={task.state.value};next={wake};goal={task.goal}",
        task.updated_at,
        task.task_id,
    )


def _trade_item(event: KrAutonomousTradeEvent, signal: KrSocialSignal | None) -> WorkspaceItemV2:
    match event:
        case KrTradeRecommendation():
            label = f"KR 가상 추천 · {event.symbol}"
            value = (
                f"virtual;entry={event.entry};stop={event.stop};targets={event.targets[0]}/{event.targets[1]};"
                f"{event.verification_state.value};valid={event.valid_until.isoformat()}"
            )
        case KrAutonomousNoTrade():
            label = f"KR 관망 · {event.symbol}"
            verification = "unknown" if signal is None else signal.verification_state.value
            value = f"virtual;reasons={','.join(item.value for item in event.reason_codes)};{verification}"
        case KrAutonomousRejected():
            label = f"KR 기각 · {event.symbol}"
            verification = "unknown" if signal is None else signal.verification_state.value
            value = f"virtual;reasons={','.join(item.value for item in event.reason_codes)};{verification}"
        case unreachable:
            assert_never(unreachable)
    return _item(
        f"kr-decision-{event.event_id[:24]}",
        label,
        "day_recommendation",
        value,
        event.timestamp,
        event.task_id,
    )


def _position_item(event: KrVirtualPositionEvent) -> WorkspaceItemV2:
    fill = "-" if event.fill_price is None else str(event.fill_price)
    exit_price = "-" if event.exit_price is None else str(event.exit_price)
    value = (
        f"virtual;state={event.state.value};entry={event.entry};stop={event.stop};"
        f"targets={event.targets[0]}/{event.targets[1]};fill={fill};exit={exit_price}"
    )
    return _item(
        f"kr-position-{event.position_id[:24]}",
        f"KR 가상 포지션 · {event.symbol}",
        "paper",
        value,
        event.occurred_at,
        event.task_id,
    )


def _item(
    item_id: str,
    label: str,
    kind: _ItemKind,
    value: str,
    observed_at: dt.datetime,
    task_id: str,
) -> WorkspaceItemV2:
    safe_label = redact_outbound_text(label, max_chars=80)
    safe_value = redact_outbound_text(value, max_chars=160)
    require_safe_outbound_text(f"{safe_label} {safe_value}")
    return WorkspaceItemV2(
        item_id=item_id,
        kind=kind,
        label=safe_label,
        state="populated",
        value=safe_value,
        observed_at=observed_at,
        trace_id=_task_node_id(task_id),
    )


def _trace(
    tasks: tuple[AutonomousResearchTask, ...],
    trades: tuple[KrAutonomousTradeEvent, ...],
    positions: tuple[KrVirtualPositionEvent, ...],
    outcomes: tuple[AutonomousMemoryRecord, ...],
    bundles: tuple[AutonomousMemoryRecord, ...],
    loop_snapshots: tuple[KrLoopCandidateSnapshot, ...],
) -> tuple[tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    nodes: dict[str, TraceNodeV2] = {}
    edges: dict[tuple[str, str], TraceEdgeV2] = {}
    for task in tasks:
        root = _task_node(nodes, task.task_id, task.created_at)
        terminal = _node(
            f"trace.kr.task_state.{task.task_id[:24]}",
            "reviewer_decision",
            task.state.value,
            task.updated_at,
            task.task_id,
        )
        nodes[terminal.node_id] = terminal
        edges[(root, terminal.node_id)] = TraceEdgeV2(
            from_node_id=root, to_node_id=terminal.node_id, kind="reviewed_by"
        )
    for event in trades:
        root = _task_node(nodes, event.task_id, event.timestamp)
        decision = _node(
            f"trace.kr.decision.{event.event_id[:24]}",
            "reviewer_decision",
            event.outcome.value,
            event.timestamp,
            event.event_id,
        )
        nodes[decision.node_id] = decision
        edges[(root, decision.node_id)] = TraceEdgeV2(from_node_id=root, to_node_id=decision.node_id, kind="decided_by")
    for event in positions:
        root = _task_node(nodes, event.task_id, event.occurred_at)
        decision_id = f"trace.kr.decision.{event.recommendation_id[:24]}"
        position = _node(
            f"trace.kr.position.{event.event_id[:24]}",
            "paper_receipt",
            event.state.value,
            event.occurred_at,
            event.event_id,
        )
        nodes[position.node_id] = position
        parent = decision_id if decision_id in nodes else root
        edges[(parent, position.node_id)] = TraceEdgeV2(
            from_node_id=parent, to_node_id=position.node_id, kind="executed_as"
        )
    append_learning_trace(
        nodes,
        edges,
        outcomes=outcomes,
        bundles=bundles,
        loop_snapshots=loop_snapshots,
    )
    return tuple(nodes.values()), tuple(edges.values())


def _task_node(nodes: dict[str, TraceNodeV2], task_id: str, at: dt.datetime) -> str:
    node_id = _task_node_id(task_id)
    nodes.setdefault(node_id, _node(node_id, "source_receipt", "durable KR research task", at, task_id))
    return node_id


def _task_node_id(task_id: str) -> str:
    return f"trace.kr.task.{task_id[:24]}"


def _node(node_id: str, kind: _NodeKind, label: str, at: dt.datetime, safe_ref: str) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label=redact_outbound_text(f"KR autonomous · {label}", max_chars=100),
        observed_at=at,
        safe_ref=safe_ref,
        state="accepted",
        source_namespace="dashboard.kr_autonomous",
    )


__all__ = ("RenderedKrAutonomousOperator", "render_kr_autonomous_operator")
