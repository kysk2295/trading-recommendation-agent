from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from trading_agent.dashboard_models_v2 import TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_projection_day_agent_support import FacadeState, day_agent_item, day_agent_trace_graph
from trading_agent.models import RecommendationEvent, RecommendationState
from trading_agent.us_day_lifecycle import (
    InvalidUsDayLifecycleError,
    UsDayLifecycleEvent,
    derive_us_day_lifecycle,
)
from trading_agent.us_day_thesis_models import UsDayTradeThesis

_EVENT_COLUMNS = ("event_id", "recommendation_id", "occurred_at", "state", "price", "note")
type _EventRow = tuple[str, str, str, float | None, str]


@dataclass(frozen=True, slots=True)
class UsDayLifecycleProjection:
    items: tuple[WorkspaceItemV2, ...]
    nodes: tuple[TraceNodeV2, ...]
    edges: tuple[TraceEdgeV2, ...]


def read_us_day_paper_events(
    path: Path,
    thesis_ids: tuple[str, ...],
) -> Mapping[str, tuple[RecommendationEvent, ...]]:
    if path.is_symlink():
        raise InvalidUsDayLifecycleError
    if not path.exists():
        return {}
    try:
        metadata = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
        ):
            raise InvalidUsDayLifecycleError
        uri = f"{path.absolute().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            _ = connection.execute("PRAGMA query_only = ON")
            columns = tuple(str(row[1]) for row in connection.execute("PRAGMA table_info(events)"))
            if columns != _EVENT_COLUMNS:
                raise sqlite3.DatabaseError
            values: dict[str, tuple[RecommendationEvent, ...]] = {}
            for thesis_id in thesis_ids:
                rows: list[_EventRow] = connection.execute(
                    "SELECT recommendation_id, occurred_at, state, price, note "
                    "FROM events WHERE recommendation_id = ? ORDER BY event_id",
                    (thesis_id,),
                ).fetchall()
                values[thesis_id] = tuple(_event_from_row(row) for row in rows)
            return values
    except (OSError, sqlite3.Error, TypeError, ValueError):
        raise InvalidUsDayLifecycleError from None


def _event_from_row(row: _EventRow) -> RecommendationEvent:
    occurred_at = dt.datetime.fromisoformat(row[1])
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise InvalidUsDayLifecycleError
    return RecommendationEvent(
        recommendation_id=row[0],
        occurred_at=occurred_at,
        state=RecommendationState(row[2]),
        price=None if row[3] is None else float(row[3]),
        note=row[4],
    )


def project_us_day_lifecycle_cards(
    theses: tuple[UsDayTradeThesis, ...],
    events_by_thesis: Mapping[str, tuple[RecommendationEvent, ...]],
    state: FacadeState,
    now: dt.datetime,
) -> UsDayLifecycleProjection:
    items: list[WorkspaceItemV2] = []
    nodes: list[TraceNodeV2] = []
    edges: list[TraceEdgeV2] = []
    ordered = sorted(theses, key=lambda item: (item.observed_at, item.thesis_id), reverse=True)[:3]
    for thesis in ordered:
        try:
            lifecycle = derive_us_day_lifecycle(thesis, events_by_thesis.get(thesis.thesis_id, ()))
            thesis_items, timeline_nodes, timeline_edges = _project_thesis(thesis, lifecycle, state, now)
        except InvalidUsDayLifecycleError:
            corrupt = _corrupt_thesis_item(thesis, now)
            thesis_items = (corrupt,)
            timeline_nodes, timeline_edges = day_agent_trace_graph(thesis_items, now)
        items.extend(thesis_items)
        nodes.extend(timeline_nodes)
        edges.extend(timeline_edges)
    return UsDayLifecycleProjection(tuple(items), tuple(nodes), tuple(edges))


def _corrupt_thesis_item(thesis: UsDayTradeThesis, now: dt.datetime) -> WorkspaceItemV2:
    digest = hashlib.sha256(thesis.thesis_id.encode()).hexdigest()
    return day_agent_item(
        f"day_agent.us.lifecycle.{digest[:32]}.corrupt",
        f"US · Alpaca Paper · {thesis.symbol or thesis.theme_name} · lifecycle corrupt",
        "corrupt",
        "paper lifecycle corrupt · no recommendation authority",
        now,
        kind="day_theme",
        trace_id=f"trace.us.lifecycle.{digest[:32]}.corrupt",
    )


def _project_thesis(
    thesis: UsDayTradeThesis,
    lifecycle: tuple[UsDayLifecycleEvent, ...],
    state: FacadeState,
    now: dt.datetime,
) -> tuple[tuple[WorkspaceItemV2, ...], tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    current = lifecycle[-1]
    digest = hashlib.sha256(thesis.thesis_id.encode()).hexdigest()
    trace_id = f"trace.us.lifecycle.{digest[:32]}"
    items = (
        day_agent_item(
            f"day_agent.us.lifecycle.{digest[:32]}",
            f"US · Alpaca Paper · {thesis.symbol or thesis.theme_name} · {current.status.value}",
            state,
            _value(thesis, current, now),
            current.occurred_at,
            kind="day_recommendation",
            trace_id=trace_id,
        ),
        day_agent_item(
            f"day_agent.us.lifecycle.{digest[:32]}.detail",
            f"US · Alpaca Paper · {thesis.symbol or thesis.theme_name} · lifecycle evidence",
            state,
            _detail(thesis, current),
            current.occurred_at,
            kind="day_theme",
            trace_id=trace_id,
        ),
    )
    event_nodes = tuple(
        TraceNodeV2(
            node_id=f"{trace_id}.{index}",
            kind="lifecycle_decision",
            label=f"US Alpaca Paper lifecycle · {event.status.value}",
            observed_at=event.occurred_at,
            safe_ref=hashlib.sha256(event.source_ref.encode()).hexdigest(),
            state="accepted",
            source_namespace="dashboard.day_agent.us",
        )
        for index, event in enumerate(lifecycle, start=1)
    )
    event_edges = tuple(
        TraceEdgeV2(
            from_node_id=event_nodes[index - 1].node_id,
            to_node_id=node.node_id,
            kind="derived_from",
        )
        for index, node in enumerate(event_nodes[1:], start=1)
    )
    source_node = TraceNodeV2(
        node_id=trace_id,
        kind="source_receipt",
        label="US immutable thesis and Alpaca Paper event ledger",
        observed_at=lifecycle[0].occurred_at,
        safe_ref=thesis.thesis_id,
        state="accepted",
        source_namespace="dashboard.day_agent.us",
    )
    terminal_node = TraceNodeV2(
        node_id=f"{trace_id}.terminal",
        kind="reviewer_decision" if state == "populated" else "blocker_terminal",
        label="US canonical lifecycle projection",
        observed_at=current.occurred_at,
        safe_ref=hashlib.sha256(current.source_ref.encode()).hexdigest(),
        state="accepted" if state == "populated" else "blocked",
        source_namespace="dashboard.day_agent.us",
    )
    boundary_edges = (
        TraceEdgeV2(from_node_id=trace_id, to_node_id=event_nodes[0].node_id, kind="derived_from"),
        TraceEdgeV2(
            from_node_id=event_nodes[-1].node_id,
            to_node_id=terminal_node.node_id,
            kind="reviewed_by" if state == "populated" else "blocked_by",
        ),
    )
    return items, (source_node, *event_nodes, terminal_node), (*boundary_edges[:1], *event_edges, boundary_edges[1])


def _value(thesis: UsDayTradeThesis, current: UsDayLifecycleEvent, now: dt.datetime) -> str:
    age = max(0, int((now - current.occurred_at).total_seconds()))
    reason = current.reason
    if thesis.entry_price is None or thesis.stop_price is None:
        return f"Alpaca Paper · {current.status.value} · reason {reason} · evidence age {age}s"
    targets = "/".join(str(item.price) for item in thesis.targets)
    return (
        f"Alpaca Paper · {current.status.value} · entry {thesis.entry_price} · stop {thesis.stop_price} · "
        f"targets {targets} · outcome {current.status.value}:{reason} · evidence age {age}s"
    )


def _detail(thesis: UsDayTradeThesis, current: UsDayLifecycleEvent) -> str:
    return (
        f"at {current.occurred_at.isoformat()} · valid {thesis.valid_until.isoformat()} · "
        f"invalidation {thesis.invalidation_rule} · rationale {thesis.rationale or current.reason}"
    )


__all__ = (
    "UsDayLifecycleProjection",
    "project_us_day_lifecycle_cards",
    "read_us_day_paper_events",
)
