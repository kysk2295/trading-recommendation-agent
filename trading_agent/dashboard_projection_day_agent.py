from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from trading_agent.dashboard_models_v2 import SourceStateName, TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_outbound_redaction import redact_outbound_text
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.day_learning_report_models import DailyLearningReport, MarketCloseReport
from trading_agent.day_learning_report_store import load_market_close_report
from trading_agent.day_learning_reports import build_daily_learning_report
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowEvent
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.research_identity_models import MarketId
from trading_agent.us_day_thesis_models import DayTradeDecision, UsDayTradeThesis
from trading_agent.us_day_thesis_store import UsDayThesisStore


@dataclass(frozen=True, slots=True)
class DayAgentFacadeProjection:
    markets: tuple[WorkspaceItemV2, ...]
    research: tuple[WorkspaceItemV2, ...]
    nodes: tuple[TraceNodeV2, ...]
    edges: tuple[TraceEdgeV2, ...]
    daily_learning_report: DailyLearningReport | None


FacadeState = Literal["populated", "unavailable", "corrupt"]


def project_day_agent_facade(outputs: Path, *, now: dt.datetime) -> DayAgentFacadeProjection:
    us_reports, us_state = _read_reports(outputs / "us_day" / "close_reports", MarketId.US_EQUITIES)
    kr_reports, kr_state = _read_reports(outputs / "kr_day" / "close_reports", MarketId.KR_EQUITIES)
    us_theses, thesis_state = _read_us_theses(outputs / "us_day" / "theses")
    kr_events, event_state = _read_kr_events(outputs / "kr_day")
    us_report = _latest(us_reports)
    kr_report = _latest(kr_reports)
    if us_report is None and kr_report is None and {us_state, kr_state, thesis_state, event_state} <= {"unavailable"}:
        return DayAgentFacadeProjection((), (), (), (), None)
    us_lane_state: FacadeState = us_state if thesis_state != "corrupt" else "corrupt"
    kr_lane_state: FacadeState = kr_state if event_state != "corrupt" else "corrupt"
    markets = (
        _us_paper_item(us_report, us_lane_state, now),
        _us_shadow_item(us_report, us_lane_state, now),
        _us_capsule_item(us_report, us_lane_state, now),
        *_us_recommendations(us_theses, us_lane_state, now),
        _kr_shadow_item(kr_report, kr_lane_state, now),
        _kr_capsule_item(kr_report, kr_events, kr_lane_state, now),
        *_kr_recommendations(kr_events, kr_lane_state, now),
    )
    research_values: list[WorkspaceItemV2] = []
    if us_report is not None or us_lane_state == "corrupt":
        research_values.extend(
            (
                _learning_item("us", "US · Shadow close learning", us_report, us_lane_state, now),
                _policy_item("us", "US · Shadow next-session policy", us_report, us_lane_state, now),
            )
        )
    if kr_report is not None or kr_lane_state == "corrupt":
        research_values.extend(
            (
                _learning_item("kr", "KR · Shadow close learning", kr_report, kr_lane_state, now),
                _policy_item("kr", "KR · Shadow next-session policy", kr_report, kr_lane_state, now),
            )
        )
    research = tuple(research_values)
    nodes, edges = _trace_graph(markets, research, now)
    daily_learning_report = (
        None
        if us_report is None or kr_report is None
        else build_daily_learning_report(us_report, kr_report, generated_at=now)
    )
    return DayAgentFacadeProjection(markets, research, nodes, edges, daily_learning_report)


def merge_day_agent_facade(
    base: WorkspaceProjection,
    facade: DayAgentFacadeProjection,
    *,
    workspace: Literal["markets", "research"],
) -> WorkspaceProjection:
    additions = facade.markets if workspace == "markets" else facade.research
    items = (*additions, *base.workspace.items)
    kept = items[:24]
    workspace_value = base.workspace.model_copy(
        update={
            "total_count": base.workspace.total_count + len(additions),
            "projected_count": len(kept),
            "truncated": len(items) > len(kept),
            "items": kept,
        }
    )
    if workspace == "research":
        return WorkspaceProjection(workspace_value, base.nodes, base.edges)
    return WorkspaceProjection(workspace_value, (*base.nodes, *facade.nodes), (*base.edges, *facade.edges))


def _read_reports(root: Path, market: MarketId) -> tuple[tuple[MarketCloseReport, ...], FacadeState]:
    if not root.exists():
        return (), "unavailable"
    try:
        reports = tuple(load_market_close_report(path) for path in root.glob("market_close_report_*.json"))
    except (OSError, ValueError):
        return (), "corrupt"
    if any(item.payload.market_id is not market for item in reports):
        return (), "corrupt"
    return reports, "populated" if reports else "unavailable"


def _read_us_theses(root: Path) -> tuple[tuple[UsDayTradeThesis, ...], FacadeState]:
    if not root.exists():
        return (), "unavailable"
    try:
        return UsDayThesisStore(root).theses(), "populated"
    except (OSError, ValueError):
        return (), "corrupt"


def _read_kr_events(root: Path) -> tuple[tuple[KrDayCapsuleShadowEvent, ...], FacadeState]:
    candidates = (root / "shadow" / "events.sqlite3", root / "capsule_shadow.sqlite3", root / "shadow.sqlite3")
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        return (), "unavailable"
    try:
        return KrDayCapsuleShadowStore(path).events(), "populated"
    except (OSError, ValueError):
        return (), "corrupt"


def _latest(reports: tuple[MarketCloseReport, ...]) -> MarketCloseReport | None:
    return max(reports, key=lambda item: (item.payload.finalized_at, item.payload.revision), default=None)


def _item(
    item_id: str,
    label: str,
    state: SourceStateName,
    value: str,
    now: dt.datetime,
) -> WorkspaceItemV2:
    return WorkspaceItemV2(
        item_id=item_id,
        kind="research",
        label=label,
        state=state,
        value=redact_outbound_text(value, max_chars=160),
        observed_at=None if state == "unavailable" else now,
        trace_id=f"trace.{item_id}",
    )


def _us_paper_item(report: MarketCloseReport | None, state: FacadeState, now: dt.datetime) -> WorkspaceItemV2:
    if report is None:
        return _item("day_agent.us.paper", "US · Alpaca Paper", state, "US close evidence unavailable", now)
    execution = report.payload.execution
    value = (
        "immutable close outcome · "
        f"filled {execution.filled_order_count} · unresolved {execution.unresolved_count} · "
        f"censored {execution.censored_count}"
    )
    return _item(
        "day_agent.us.paper",
        "US · Alpaca Paper",
        state,
        value,
        now,
    )


def _us_shadow_item(report: MarketCloseReport | None, state: FacadeState, now: dt.datetime) -> WorkspaceItemV2:
    return _item(
        "day_agent.us.shadow",
        "US · Shadow",
        state,
        "verified report unavailable" if report is None else _learning_value(report),
        now,
    )


def _us_capsule_item(report: MarketCloseReport | None, state: FacadeState, now: dt.datetime) -> WorkspaceItemV2:
    return _capsule_item("day_agent.us.capsules", "US · Shadow capsule states", report, state, now, suspended=0)


def _kr_shadow_item(report: MarketCloseReport | None, state: FacadeState, now: dt.datetime) -> WorkspaceItemV2:
    return _item(
        "day_agent.kr.shadow",
        "KR · Shadow · provider read-only",
        state,
        "KR evidence invalid"
        if state == "corrupt"
        else "verified report unavailable"
        if report is None
        else _learning_value(report),
        now,
    )


def _kr_capsule_item(
    report: MarketCloseReport | None,
    events: tuple[KrDayCapsuleShadowEvent, ...],
    state: FacadeState,
    now: dt.datetime,
) -> WorkspaceItemV2:
    suspended = sum(event.status.value in {"blocked", "failed", "censored"} for event in events)
    return _capsule_item("day_agent.kr.capsules", "KR · Shadow capsule states", report, state, now, suspended=suspended)


def _capsule_item(
    item_id: str,
    label: str,
    report: MarketCloseReport | None,
    state: FacadeState,
    now: dt.datetime,
    *,
    suspended: int,
) -> WorkspaceItemV2:
    if report is None:
        value = "active 0 · queued 0 · suspended 0"
    else:
        next_session = report.payload.next_session
        value = (
            f"active {len(next_session.active_capsule_ids)} · "
            f"queued {len(next_session.queued_capsule_ids)} · suspended {suspended}"
        )
    return _item(item_id, label, state, value, now)


def _us_recommendations(
    theses: tuple[UsDayTradeThesis, ...], state: FacadeState, now: dt.datetime
) -> tuple[WorkspaceItemV2, ...]:
    recommendations = sorted(
        (item for item in theses if item.decision is DayTradeDecision.RECOMMEND),
        key=lambda item: (item.observed_at, item.thesis_id),
        reverse=True,
    )[:3]
    values: list[WorkspaceItemV2] = []
    for position, thesis in enumerate(recommendations, start=1):
        assert thesis.symbol is not None and thesis.entry_price is not None and thesis.stop_price is not None
        targets = "/".join(str(target.price) for target in thesis.targets)
        rationale = (
            thesis.flow_rationale or thesis.leader_rationale or thesis.catalyst_rationale or thesis.theme_rationale
        )
        reason = "rationale withheld" if rationale is None else rationale.text
        values.append(
            _item(
                f"day_agent.us.recommendation.{position}",
                f"US · Shadow · {thesis.symbol}",
                state,
                f"entry {thesis.entry_price} · stop {thesis.stop_price} · targets {targets} · {reason}",
                now,
            )
        )
    return tuple(values)


def _kr_recommendations(
    events: tuple[KrDayCapsuleShadowEvent, ...], state: FacadeState, now: dt.datetime
) -> tuple[WorkspaceItemV2, ...]:
    latest: dict[str, KrDayCapsuleShadowEvent] = {}
    for event in events:
        latest[event.capsule_id] = event
    items: list[WorkspaceItemV2] = []
    for position, event in enumerate(
        sorted(latest.values(), key=lambda item: item.occurred_at, reverse=True)[:3], start=1
    ):
        if event.entry_price is None or event.stop_price is None:
            value = f"{event.symbol} · outcome {event.status.value}/{event.reason.value} · no entry"
        else:
            targets = "/".join(str(target) for target in event.target_prices)
            value = (
                f"{event.symbol} · entry {event.entry_price} · stop {event.stop_price} · "
                f"targets {targets} · rationale {event.reason.value} · outcome {event.status.value}"
            )
        items.append(
            _item(f"day_agent.kr.recommendation.{position}", "KR · Shadow · provider read-only", state, value, now)
        )
    return tuple(items)


def _learning_item(
    prefix: str, label: str, report: MarketCloseReport | None, state: FacadeState, now: dt.datetime
) -> WorkspaceItemV2:
    return _item(
        f"day_agent.{prefix}.learning",
        label,
        state,
        "close learning unavailable" if report is None else _learning_value(report),
        now,
    )


def _learning_value(report: MarketCloseReport) -> str:
    research = report.payload.research
    return (
        f"close learning · supported {research.supported_count} · "
        f"refuted {research.refuted_count} · inconclusive {research.inconclusive_count}"
    )


def _policy_item(
    prefix: str, label: str, report: MarketCloseReport | None, state: FacadeState, now: dt.datetime
) -> WorkspaceItemV2:
    if report is None:
        value = "next-session policy unavailable"
    else:
        reasons = ", ".join(report.payload.next_session.reason_codes)
        value = f"next session · {reasons} · report {report.report_id[:12]}"
    return _item(f"day_agent.{prefix}.policy", label, state, value, now)


def _trace_graph(
    markets: tuple[WorkspaceItemV2, ...], research: tuple[WorkspaceItemV2, ...], now: dt.datetime
) -> tuple[tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    nodes: list[TraceNodeV2] = []
    edges: list[TraceEdgeV2] = []
    for item in (*markets, *research):
        safe_ref = hashlib.sha256(f"{item.item_id}:{item.value}".encode()).hexdigest()
        terminal = f"{item.trace_id}.terminal"
        blocked = item.state in {"blocked", "unavailable", "corrupt", "error"}
        nodes.extend(
            (
                TraceNodeV2(
                    node_id=item.trace_id,
                    kind="source_receipt",
                    label=item.label,
                    observed_at=now,
                    safe_ref=safe_ref,
                    state="unavailable" if blocked else "accepted",
                    source_namespace="dashboard.day_agent",
                ),
                TraceNodeV2(
                    node_id=terminal,
                    kind="blocker_terminal" if blocked else "reviewer_decision",
                    label=f"{item.label} projection",
                    observed_at=now,
                    safe_ref=safe_ref,
                    state="blocked" if blocked else "accepted",
                    source_namespace="dashboard.day_agent",
                ),
            )
        )
        edges.append(
            TraceEdgeV2(
                from_node_id=item.trace_id, to_node_id=terminal, kind="blocked_by" if blocked else "reviewed_by"
            )
        )
    return tuple(nodes), tuple(edges)


__all__ = ("DayAgentFacadeProjection", "merge_day_agent_facade", "project_day_agent_facade")
