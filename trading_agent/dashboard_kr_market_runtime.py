from __future__ import annotations

import datetime as dt
import hashlib

from trading_agent.dashboard_kr_autonomous_bridge import (
    InvalidKrAutonomousBridgeError,
    current_kr_databases,
)
from trading_agent.dashboard_models_v2 import (
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.kr_theme_store import KrThemeReader


def project_kr_realtime_cycle(
    outputs,
    *,
    now: dt.datetime,
) -> tuple[
    WorkspaceItemV2,
    tuple[TraceNodeV2, ...],
    tuple[TraceEdgeV2, ...],
]:
    source_id = "trace.markets.kr_realtime.source"
    terminal_id = "trace.markets.kr_realtime.terminal"
    try:
        candidates = tuple(
            (cycle, reader.source_runs(cycle.collection_cycle_id))
            for database in current_kr_databases(
                outputs,
                now.astimezone(dt.timezone(dt.timedelta(hours=9))).date(),
            )
            for reader in (KrThemeReader(database),)
            for cycle in reader.cycles()
            if cycle.completed_at <= now + dt.timedelta(minutes=5)
        )
    except (InvalidKrAutonomousBridgeError, OSError, ValueError):
        candidates = ()
    if not candidates:
        safe_ref = hashlib.sha256(b"kr-realtime-cycle-missing").hexdigest()
        return (
            WorkspaceItemV2(
                item_id="market.kr.realtime_cycle",
                kind="metric",
                label="KR realtime detection",
                state="unavailable",
                value=None,
                observed_at=None,
                trace_id=source_id,
            ),
            (
                _node(source_id, "source_receipt", now, safe_ref, "unavailable"),
                _node(terminal_id, "blocker_terminal", now, safe_ref, "blocked"),
            ),
            (
                TraceEdgeV2(
                    from_node_id=source_id,
                    to_node_id=terminal_id,
                    kind="blocked_by",
                ),
            ),
        )
    cycle, runs = max(candidates, key=lambda item: item[0].completed_at)
    success = sum(run.status.value == "success" for run in runs)
    records = sum(run.record_count for run in runs)
    stale = now - cycle.completed_at > dt.timedelta(minutes=15)
    state = "blocked" if not cycle.complete else "stale" if stale else "populated"
    safe_ref = hashlib.sha256(cycle.model_dump_json().encode()).hexdigest()
    nodes = [_node(source_id, "source_receipt", cycle.completed_at, safe_ref, "accepted")]
    edges: tuple[TraceEdgeV2, ...] = ()
    if state == "blocked":
        nodes.append(
            _node(
                terminal_id,
                "blocker_terminal",
                cycle.completed_at,
                safe_ref,
                "blocked",
            )
        )
        edges = (
            TraceEdgeV2(
                from_node_id=source_id,
                to_node_id=terminal_id,
                kind="blocked_by",
            ),
        )
    return (
        WorkspaceItemV2(
            item_id="market.kr.realtime_cycle",
            kind="metric",
            label="KR realtime detection",
            state=state,
            value=(
                f"records={records};coverage={success}/{len(runs)};"
                f"cycle={cycle.collection_cycle_id}"
            ),
            observed_at=cycle.completed_at,
            trace_id=source_id,
        ),
        tuple(nodes),
        edges,
    )


def _node(
    node_id: str,
    kind,
    observed_at: dt.datetime,
    safe_ref: str,
    state,
) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label="KR realtime source cycle",
        observed_at=observed_at,
        safe_ref=safe_ref,
        state=state,
        source_namespace="dashboard.kr_realtime",
    )


__all__ = ("project_kr_realtime_cycle",)
