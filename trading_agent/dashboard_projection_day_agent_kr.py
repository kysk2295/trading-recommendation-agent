from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path

from trading_agent.dashboard_models_v2 import TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_projection_day_agent_kr_render import project_kr_thesis
from trading_agent.dashboard_projection_day_agent_kr_validation import has_bound_history
from trading_agent.dashboard_projection_day_agent_support import (
    FacadeState,
    InvalidKrDayLifecycleProjectionError,
    day_agent_item,
)
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowEvent
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_decision_delivery_identity import same_kr_day_thesis
from trading_agent.kr_day_decision_delivery_record_builders import bound_kr_day_decision_id
from trading_agent.kr_day_decision_models import KrDayDecisionEvent
from trading_agent.kr_day_decision_store import KrDayDecisionStore


@dataclass(frozen=True, slots=True)
class KrDayLifecycleProjection:
    items: tuple[WorkspaceItemV2, ...]
    nodes: tuple[TraceNodeV2, ...]
    edges: tuple[TraceEdgeV2, ...]
    shadow_events: tuple[KrDayCapsuleShadowEvent, ...]
    shadow_state: FacadeState


def project_kr_day_lifecycle(root: Path, *, now: dt.datetime) -> KrDayLifecycleProjection:
    decisions, decision_state = _read_decisions(root)
    shadows, shadow_state = _read_shadows(root)
    if decision_state == "corrupt":
        item = _source_failure("decision", "decision ledger corrupt", now)
        return KrDayLifecycleProjection((item,), *_trace_failure(item, now), shadows, shadow_state)
    if shadow_state == "corrupt":
        item = _source_failure("shadow", "shadow ledger corrupt", now)
        return KrDayLifecycleProjection((item,), *_trace_failure(item, now), shadows, shadow_state)
    if not decisions:
        return _without_decisions(shadows, shadow_state, now)
    decision_ids = frozenset(event.event_id for event in decisions)
    groups = tuple(
        (
            history,
            tuple(event for event in shadows if _belongs_to_history(event, history, decision_ids)),
            has_bound_history(
                history,
                tuple(event for event in shadows if _belongs_to_history(event, history, decision_ids)),
            ),
        )
        for history in sorted(_decision_groups(decisions), key=lambda value: value[-1].observed_at, reverse=True)
    )
    items: list[WorkspaceItemV2] = []
    nodes: list[TraceNodeV2] = []
    edges: list[TraceEdgeV2] = []
    claimed_shadow_ids = {event.event_id for _, shadow_history, _ in groups for event in shadow_history}
    for index, (history, shadow_history, is_bound) in enumerate(groups):
        if is_bound and index >= 3:
            continue
        thesis_root = history[0]
        try:
            if not is_bound:
                raise InvalidKrDayLifecycleProjectionError
            card_items, card_nodes, card_edges = project_kr_thesis(history, shadow_history, now)
        except InvalidKrDayLifecycleProjectionError:
            item = _thesis_failure(thesis_root, now)
            card_items, card_nodes, card_edges = (item,), *_trace_failure(item, now)
        items.extend(card_items)
        nodes.extend(card_nodes)
        edges.extend(card_edges)
    unbound = tuple(event for event in shadows if event.event_id not in claimed_shadow_ids)
    if unbound:
        item = day_agent_item(
            "day_agent.kr.lifecycle.unbound",
            "KR · Legacy shadow evidence",
            "blocked",
            "legacy shadow unbound · no recommendation claim · SHADOW/PAPER ONLY",
            unbound[-1].occurred_at,
        )
        failure_nodes, failure_edges = _trace_failure(item, unbound[-1].occurred_at)
        items.append(item)
        nodes.extend(failure_nodes)
        edges.extend(failure_edges)
    return KrDayLifecycleProjection(tuple(items), tuple(nodes), tuple(edges), shadows, shadow_state)


def _read_decisions(root: Path) -> tuple[tuple[KrDayDecisionEvent, ...], FacadeState]:
    try:
        path = _single_existing(
            root,
            ("kr-day-decisions.sqlite3", "decisions.sqlite3", "decision/events.sqlite3"),
        )
        if path is None:
            return (), "unavailable"
        events = KrDayDecisionStore(path).events()
    except (OSError, ValueError):
        return (), "corrupt"
    return events, "populated" if events else "unavailable"


def _read_shadows(root: Path) -> tuple[tuple[KrDayCapsuleShadowEvent, ...], FacadeState]:
    try:
        path = _single_existing(
            root,
            (
                "kr-day-capsule-shadow.sqlite3",
                "shadow/events.sqlite3",
                "capsule_shadow.sqlite3",
                "shadow.sqlite3",
            ),
        )
        if path is None:
            return (), "unavailable"
        events = KrDayCapsuleShadowStore(path).events()
    except (OSError, ValueError):
        return (), "corrupt"
    return events, "populated" if events else "unavailable"


def _single_existing(root: Path, candidates: tuple[str, ...]) -> Path | None:
    existing = tuple(root / candidate for candidate in candidates if (root / candidate).exists())
    if len(existing) > 1:
        raise ValueError
    return existing[0] if existing else None


def _without_decisions(
    shadows: tuple[KrDayCapsuleShadowEvent, ...], state: FacadeState, now: dt.datetime
) -> KrDayLifecycleProjection:
    if not shadows:
        return KrDayLifecycleProjection((), (), (), (), state)
    item = day_agent_item(
        "day_agent.kr.lifecycle.unbound",
        "KR · Legacy shadow evidence",
        "blocked",
        "legacy shadow unbound · no recommendation claim · SHADOW/PAPER ONLY",
        shadows[-1].occurred_at,
    )
    return KrDayLifecycleProjection((item,), *_trace_failure(item, now), shadows, state)


def _belongs_to_history(
    shadow: KrDayCapsuleShadowEvent,
    history: tuple[KrDayDecisionEvent, ...],
    decision_ids: frozenset[str],
) -> bool:
    bound_id = bound_kr_day_decision_id(shadow)
    history_ids = frozenset(event.event_id for event in history)
    if bound_id in decision_ids:
        return bound_id in history_ids
    root = history[0]
    return (
        shadow.capsule_id == root.capsule_id
        and shadow.session_date == root.session_date
        and shadow.symbol == root.symbol
    )


def _decision_groups(decisions: tuple[KrDayDecisionEvent, ...]) -> tuple[tuple[KrDayDecisionEvent, ...], ...]:
    groups: list[list[KrDayDecisionEvent]] = []
    for decision in decisions:
        group = next((value for value in groups if same_kr_day_thesis(value[0], decision)), None)
        if group is None:
            groups.append([decision])
        else:
            group.append(decision)
    return tuple(tuple(group) for group in groups)


def _source_failure(kind: str, value: str, observed_at: dt.datetime) -> WorkspaceItemV2:
    return day_agent_item(
        f"day_agent.kr.lifecycle.{kind}",
        "KR · Decision lifecycle",
        "corrupt",
        f"{value} · SHADOW/PAPER ONLY · no recommendation authority",
        observed_at,
    )


def _thesis_failure(root: KrDayDecisionEvent, observed_at: dt.datetime) -> WorkspaceItemV2:
    digest = hashlib.sha256(
        f"{root.capsule_id}:{root.hypothesis_version_id}:{root.opportunity_id}:{root.session_date}".encode()
    ).hexdigest()
    return day_agent_item(
        f"day_agent.kr.lifecycle.{digest}.corrupt",
        f"KR · {root.symbol} · lifecycle corrupt",
        "corrupt",
        "decision/shadow binding corrupt · SHADOW/PAPER ONLY · no recommendation authority",
        observed_at,
        trace_id=f"trace.kr.lifecycle.{digest}",
    )


def _trace_failure(
    item: WorkspaceItemV2, observed_at: dt.datetime
) -> tuple[tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    safe_ref = hashlib.sha256(f"{item.item_id}:{item.value}".encode()).hexdigest()
    source = TraceNodeV2(
        node_id=item.trace_id,
        kind="source_receipt",
        label=item.label,
        observed_at=observed_at,
        safe_ref=safe_ref,
        state="unavailable",
        source_namespace="dashboard.day_agent.kr",
    )
    terminal = TraceNodeV2(
        node_id=f"{item.trace_id}.terminal",
        kind="blocker_terminal",
        label=f"{item.label} blocked",
        observed_at=observed_at,
        safe_ref=safe_ref,
        state="blocked",
        source_namespace="dashboard.day_agent.kr",
    )
    edge = TraceEdgeV2(from_node_id=source.node_id, to_node_id=terminal.node_id, kind="blocked_by")
    return (source, terminal), (edge,)


__all__ = ("KrDayLifecycleProjection", "project_kr_day_lifecycle")
