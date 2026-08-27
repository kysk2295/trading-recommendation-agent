from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Literal

from trading_agent.autonomous_memory_models import AutonomousMemoryRecord, AutonomousMemoryScope
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_task_models import AutonomousResearchTask
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.dashboard_kr_autonomous_operator_render import render_kr_autonomous_operator
from trading_agent.dashboard_models_v2 import TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.kr_autonomous_operator_paths import KrAutonomousOperatorPaths
from trading_agent.kr_autonomous_outcome_models import KrLoopFailureCode
from trading_agent.kr_autonomous_trade_models import KrAutonomousTradeEvent
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_loop_engineer_models import KrLoopCandidateSnapshot
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore
from trading_agent.kr_social_signal_models import KrSocialSignal
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.kr_virtual_position_models import KrVirtualPositionEvent
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore

_CAP = 8


@dataclass(frozen=True, slots=True)
class KrAutonomousDashboardSlice:
    items: tuple[WorkspaceItemV2, ...]
    total_count: int


@dataclass(frozen=True, slots=True)
class KrAutonomousDashboardProjection:
    markets: KrAutonomousDashboardSlice
    research: KrAutonomousDashboardSlice
    paper: KrAutonomousDashboardSlice
    nodes: tuple[TraceNodeV2, ...]
    edges: tuple[TraceEdgeV2, ...]


def project_kr_autonomous_operator(
    paths: KrAutonomousOperatorPaths,
    *,
    now: dt.datetime,
) -> KrAutonomousDashboardProjection:
    tasks = _latest_tasks(AutonomousTaskStore(paths.task_database).reader().tasks())
    trades = _latest_trades(KrAutonomousTradeStore(paths.trade_database).events())
    positions = _latest_positions(KrVirtualPositionStore(paths.position_database).all_events())
    outcomes, bundles = _memory_records(paths, trades)
    loop_snapshots = _loop_snapshots(paths)
    signals = _signals(paths, trades)
    rendered = render_kr_autonomous_operator(
        tasks=tasks[:_CAP],
        trades=trades[:_CAP],
        positions=positions[:_CAP],
        outcomes=outcomes[:_CAP],
        bundles=bundles[:_CAP],
        loop_snapshots=loop_snapshots[:_CAP],
        signals=signals,
        now=now,
    )
    return KrAutonomousDashboardProjection(
        markets=KrAutonomousDashboardSlice(rendered.markets, len(trades)),
        research=KrAutonomousDashboardSlice(
            rendered.research,
            len(tasks) + len(outcomes) + len(bundles) + len(loop_snapshots),
        ),
        paper=KrAutonomousDashboardSlice(rendered.paper, len(positions)),
        nodes=rendered.nodes,
        edges=rendered.edges,
    )


def merge_kr_autonomous_operator(
    base: WorkspaceProjection,
    projection: KrAutonomousDashboardProjection,
    *,
    workspace: Literal["markets", "research", "paper"],
) -> WorkspaceProjection:
    addition = getattr(projection, workspace)
    if not addition.items:
        nodes = (*base.nodes, *projection.nodes) if workspace == "markets" else base.nodes
        edges = (*base.edges, *projection.edges) if workspace == "markets" else base.edges
        return WorkspaceProjection(base.workspace, nodes, edges)
    items = (*addition.items, *base.workspace.items)
    kept = items[:24]
    merged = base.workspace.model_copy(
        update={
            "total_count": base.workspace.total_count + addition.total_count,
            "projected_count": len(kept),
            "truncated": base.workspace.total_count + addition.total_count > len(kept),
            "items": kept,
        }
    )
    nodes = (*base.nodes, *projection.nodes) if workspace == "markets" else base.nodes
    edges = (*base.edges, *projection.edges) if workspace == "markets" else base.edges
    return WorkspaceProjection(merged, nodes, edges)


def _latest_tasks(tasks: tuple[AutonomousResearchTask, ...]) -> tuple[AutonomousResearchTask, ...]:
    return tuple(
        sorted(
            (task for task in tasks if task.market_scope == "kr_equities"),
            key=lambda item: (item.updated_at, item.task_id),
            reverse=True,
        )
    )


def _latest_trades(trades: tuple[KrAutonomousTradeEvent, ...]) -> tuple[KrAutonomousTradeEvent, ...]:
    return tuple(sorted(trades, key=lambda item: (item.timestamp, item.event_id), reverse=True))


def _latest_positions(
    events: tuple[KrVirtualPositionEvent, ...],
) -> tuple[KrVirtualPositionEvent, ...]:
    latest: dict[str, KrVirtualPositionEvent] = {}
    for event in events:
        latest[event.position_id] = event
    return tuple(sorted(latest.values(), key=lambda item: (item.occurred_at, item.event_id), reverse=True))


def _memory_records(
    paths: KrAutonomousOperatorPaths,
    trades: tuple[KrAutonomousTradeEvent, ...],
) -> tuple[tuple[AutonomousMemoryRecord, ...], tuple[AutonomousMemoryRecord, ...]]:
    reader = AutonomousMemoryStore(paths.memory_database).reader()
    records = {
        record.memory_id: record
        for event in trades
        for record in reader.history(f"market.kr.{event.symbol}.{event.event_id[:24]}")
    }
    for symbol in sorted({event.symbol for event in trades}):
        digest = hashlib.sha256(f"symbol:{symbol}".encode()).hexdigest()[:16]
        for failure in KrLoopFailureCode:
            key = f"self_improvement.kr.{failure.value}.{digest}"
            records.update((record.memory_id, record) for record in reader.history(key))
    failure_refs = tuple(sorted(f"failure:{failure.value}" for failure in KrLoopFailureCode))
    records.update(
        (record.memory_id, record)
        for record in reader.search(AutonomousMemoryScope.SELF_IMPROVEMENT, failure_refs, limit=32)
    )
    latest = {record.memory_key: record for record in records.values()}
    ordered = tuple(sorted(latest.values(), key=lambda item: (item.recorded_at, item.memory_id), reverse=True))
    return (
        tuple(item for item in ordered if item.scope is AutonomousMemoryScope.MARKET),
        tuple(item for item in ordered if item.scope is AutonomousMemoryScope.SELF_IMPROVEMENT),
    )


def _loop_snapshots(paths: KrAutonomousOperatorPaths) -> tuple[KrLoopCandidateSnapshot, ...]:
    return tuple(
        sorted(
            KrLoopEngineerStore(paths.loop_database).snapshots(),
            key=lambda item: (item.updated_at, item.snapshot_id),
            reverse=True,
        )
    )


def _signals(
    paths: KrAutonomousOperatorPaths,
    trades: tuple[KrAutonomousTradeEvent, ...],
) -> tuple[KrSocialSignal, ...]:
    store = KrSocialSignalStore(paths.social_signal_database)
    values = {
        signal.signal_id: signal
        for task_id in {event.task_id for event in trades}
        for signal in store.signals_for_task(task_id)
    }
    return tuple(values.values())


__all__ = (
    "KrAutonomousDashboardProjection",
    "KrAutonomousDashboardSlice",
    "merge_kr_autonomous_operator",
    "project_kr_autonomous_operator",
)
