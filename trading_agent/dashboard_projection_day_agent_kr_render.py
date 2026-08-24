from __future__ import annotations

import datetime as dt
import hashlib
from typing import assert_never

from trading_agent.dashboard_models_v2 import TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_projection_day_agent_support import (
    InvalidKrDayLifecycleProjectionError,
    day_agent_item,
)
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowEvent, KrDayCapsuleShadowStatus
from trading_agent.kr_day_decision_models import KrDayDecisionEvent, KrDayDecisionStatus


def project_kr_thesis(
    decisions: tuple[KrDayDecisionEvent, ...],
    shadows: tuple[KrDayCapsuleShadowEvent, ...],
    now: dt.datetime,
) -> tuple[tuple[WorkspaceItemV2, ...], tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    decision = decisions[-1]
    latest_shadow = shadows[-1] if shadows else None
    if latest_shadow is not None and latest_shadow.status in {
        KrDayCapsuleShadowStatus.ACTIVE,
        KrDayCapsuleShadowStatus.STOPPED,
        KrDayCapsuleShadowStatus.TARGETED,
        KrDayCapsuleShadowStatus.CENSORED,
    }:
        if decision.status in {KrDayDecisionStatus.REJECTED, KrDayDecisionStatus.BLOCKED}:
            raise InvalidKrDayLifecycleProjectionError
        observed_at = latest_shadow.occurred_at
        status = latest_shadow.status.value.upper()
    else:
        observed_at = decision.observed_at
        status = decision.status.value
    digest = hashlib.sha256(
        f"{decision.capsule_id}:{decision.hypothesis_version_id}:{decision.opportunity_id}:{decision.session_date}".encode()
    ).hexdigest()
    trace_id = f"trace.kr.lifecycle.{digest}"
    main = day_agent_item(
        f"day_agent.kr.lifecycle.{digest}",
        f"KR · {decision.symbol} · {status}",
        "populated",
        _card_value(decision, latest_shadow, observed_at, now, status),
        observed_at,
        kind="day_recommendation",
        trace_id=trace_id,
    )
    details = _detail_items(digest, trace_id, decision, latest_shadow, observed_at)
    nodes, edges = _timeline(trace_id, decisions, shadows)
    return (main, *details), nodes, edges


def _card_value(
    decision: KrDayDecisionEvent,
    shadow: KrDayCapsuleShadowEvent | None,
    observed_at: dt.datetime,
    now: dt.datetime,
    status: str,
) -> str:
    age = max(0, int((now - observed_at).total_seconds()))
    stamp = decision.completed_bar_at.astimezone(dt.UTC).strftime("%m-%d %H:%MZ")
    meta = (
        f"SHADOW/PAPER ONLY · KRX {stamp} · cap {decision.capsule_id[:8]}/"
        f"hyp {decision.hypothesis_version_id[:8]} · evidence age {age}s · {status}"
    )
    if shadow is not None and shadow.entry_price is not None and shadow.stop_price is not None:
        targets = "/".join(str(value) for value in shadow.target_prices)
        return f"{meta} · fill {shadow.entry_price} stop {shadow.stop_price} targets {targets}"
    plan = decision.conditional_plan
    if plan is not None:
        targets = "/".join(str(value) for value in plan.target_prices)
        return f"{meta} · entry {plan.trigger_price} stop {plan.stop_price} targets {targets}"
    return meta


def _detail_items(
    digest: str,
    trace_id: str,
    decision: KrDayDecisionEvent,
    shadow: KrDayCapsuleShadowEvent | None,
    observed_at: dt.datetime,
) -> tuple[WorkspaceItemV2, ...]:
    values = _detail_values(decision, shadow)
    values.append(f"KRX {decision.completed_bar_at.isoformat()} · evidence refs {','.join(decision.evidence_refs)}")
    return tuple(
        day_agent_item(
            f"day_agent.kr.lifecycle.{digest}.detail.{index}",
            f"KR · {decision.symbol} · lifecycle evidence {index}",
            "populated",
            value,
            observed_at,
            kind="day_theme",
            trace_id=trace_id,
        )
        for index, value in enumerate(values, start=1)
    )


def _detail_values(decision: KrDayDecisionEvent, shadow: KrDayCapsuleShadowEvent | None) -> list[str]:
    match shadow:
        case None:
            return _decision_details(decision)
        case KrDayCapsuleShadowEvent():
            match shadow.status:
                case KrDayCapsuleShadowStatus.ACTIVE:
                    return [
                        f"fill time {shadow.occurred_at.isoformat()} · "
                        "unrealized unavailable (no current-price evidence)"
                    ]
                case (
                    KrDayCapsuleShadowStatus.STOPPED
                    | KrDayCapsuleShadowStatus.TARGETED
                    | KrDayCapsuleShadowStatus.CENSORED
                ):
                    return [
                        f"outcome {shadow.status.value.upper()} · reason {shadow.reason.value} · immutable timeline"
                    ]
                case KrDayCapsuleShadowStatus.BLOCKED | KrDayCapsuleShadowStatus.FAILED:
                    return [f"shadow {shadow.status.value.upper()} · reason {shadow.reason.value} · immutable evidence"]
                case KrDayCapsuleShadowStatus.REGISTERED:
                    return _decision_details(decision)
                case unreachable:
                    assert_never(unreachable)


def _decision_details(decision: KrDayDecisionEvent) -> list[str]:
    match decision.status:
        case KrDayDecisionStatus.INVESTIGATING:
            evidence = ",".join(f"{item.name}={item.value}" for item in decision.observed_evidence) or "none"
            missing = ",".join(reason.value for reason in decision.reason_codes)
            return [f"current {evidence} · missing {missing}"]
        case KrDayDecisionStatus.ARMED:
            plan = decision.conditional_plan
            if plan is None:
                raise InvalidKrDayLifecycleProjectionError
            return [
                f"trigger {plan.trigger_rule} · invalidation {plan.invalidation_rule}",
                f"valid {plan.valid_until.isoformat()} · rationale {plan.rationale}",
            ]
        case KrDayDecisionStatus.REJECTED | KrDayDecisionStatus.BLOCKED | KrDayDecisionStatus.EXPIRED:
            reasons = ",".join(reason.value for reason in decision.reason_codes)
            return [f"reasons {reasons} · evidence {decision.evidence_refs[0]}"]
        case unreachable:
            assert_never(unreachable)


def _timeline(
    trace_id: str,
    decisions: tuple[KrDayDecisionEvent, ...],
    shadows: tuple[KrDayCapsuleShadowEvent, ...],
) -> tuple[tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    events = tuple(
        (f"trace.kr.decision.{event.event_id}", event.status.value, event.observed_at, event.event_id)
        for event in decisions
    ) + tuple(
        (f"trace.kr.shadow.{event.event_id}", event.status.value, event.occurred_at, event.event_id)
        for event in shadows
    )
    nodes = tuple(
        TraceNodeV2(
            node_id=node_id,
            kind="lifecycle_decision",
            label=f"KR immutable lifecycle · {status}",
            observed_at=observed_at,
            safe_ref=event_id,
            state="accepted",
            source_namespace="dashboard.day_agent.kr",
        )
        for node_id, status, observed_at, event_id in events
    )
    item_node = TraceNodeV2(
        node_id=trace_id,
        kind="source_receipt",
        label="KR immutable decision and shadow ledgers",
        observed_at=events[0][2],
        safe_ref=events[0][3],
        state="accepted",
        source_namespace="dashboard.day_agent.kr",
    )
    terminal_node = TraceNodeV2(
        node_id=f"{trace_id}.terminal",
        kind="reviewer_decision",
        label="KR canonical lifecycle projection",
        observed_at=events[-1][2],
        safe_ref=events[-1][3],
        state="accepted",
        source_namespace="dashboard.day_agent.kr",
    )
    lineage_edges = tuple(
        TraceEdgeV2(from_node_id=events[index - 1][0], to_node_id=event[0], kind="derived_from")
        for index, event in enumerate(events[1:], start=1)
    )
    source_edge = TraceEdgeV2(from_node_id=trace_id, to_node_id=events[0][0], kind="derived_from")
    terminal_edge = TraceEdgeV2(from_node_id=events[-1][0], to_node_id=terminal_node.node_id, kind="reviewed_by")
    return (item_node, *nodes, terminal_node), (source_edge, *lineage_edges, terminal_edge)


__all__ = ("project_kr_thesis",)
