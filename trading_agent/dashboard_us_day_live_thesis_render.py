from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import assert_never

from trading_agent.dashboard_models_v2 import TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_outbound_redaction import redact_outbound_text
from trading_agent.dashboard_us_day_live_primitives import day_live_item, day_live_node
from trading_agent.dashboard_us_day_paper import FinalizedPaperProjectionBundle, canonical_day_exit_event
from trading_agent.models import RecommendationEvent
from trading_agent.paper_execution_models import IntentId
from trading_agent.us_day_lifecycle import derive_us_day_lifecycle
from trading_agent.us_day_thesis_models import DayTradeDecision, UsDayThesisChange, UsDayTradeThesis


@dataclass(frozen=True, slots=True)
class CorruptDayThesisProjection:
    item: WorkspaceItemV2
    nodes: tuple[TraceNodeV2, ...]
    edges: tuple[TraceEdgeV2, ...]


def render_us_day_thesis_items(
    thesis: UsDayTradeThesis,
    changes: tuple[UsDayThesisChange, ...],
    paper_events: tuple[RecommendationEvent, ...],
    index: int,
    source: str,
) -> tuple[WorkspaceItemV2, ...]:
    current = derive_us_day_lifecycle(thesis, paper_events)[-1]
    match thesis.decision:
        case DayTradeDecision.RECOMMEND:
            assert thesis.symbol is not None and thesis.entry_price is not None and thesis.stop_price is not None
            targets = "/".join(str(item.price) for item in thesis.targets)
            items = [
                day_live_item(
                    f"day.recommendation.{thesis.symbol}",
                    "day_recommendation",
                    f"{thesis.symbol} {current.status.value}",
                    f"entry {thesis.entry_price} · stop {thesis.stop_price} · targets {targets}",
                    current.occurred_at,
                    source,
                )
            ]
            if changes:
                change = max(changes, key=lambda item: (item.occurred_at, item.event_id))
                items.append(
                    day_live_item(
                        f"day.thesis_change.{thesis.symbol}",
                        "day_recommendation",
                        f"{thesis.symbol} thesis change",
                        change.kind.value,
                        change.occurred_at,
                        source,
                    )
                )
            return tuple(items)
        case DayTradeDecision.NO_TRADE:
            return _terminal_thesis_item(thesis, current.status.value, index, "day.no_trade", source)
        case DayTradeDecision.WATCH | DayTradeDecision.INSUFFICIENT_EVIDENCE:
            return _terminal_thesis_item(thesis, current.status.value, index, "day.terminal", source)
        case unreachable:
            assert_never(unreachable)


def render_us_day_paper_items(
    thesis: UsDayTradeThesis, paper_ledger: FinalizedPaperProjectionBundle, source: str
) -> tuple[WorkspaceItemV2, ...]:
    assert thesis.symbol is not None
    ledger = paper_ledger.ledger
    intent_id = IntentId(thesis.thesis_id)
    state = next((item for item in ledger.order_states if item.intent_id == intent_id), None)
    if state is None:
        return ()
    values = ["filled" if intent_id in ledger.filled_intent_ids else "submitted"]
    protected = any(item.plan.parent_intent_id == intent_id for item in ledger.protective_oco_plans)
    if protected:
        values.append("protected")
    if intent_id not in ledger.unresolved_intent_ids:
        values.append("reconciled")
    item = day_live_item(
        f"day.paper.{thesis.symbol}",
        "paper",
        f"{thesis.symbol} Paper lifecycle",
        " · ".join(values),
        thesis.observed_at,
        source,
    )
    exit_event = canonical_day_exit_event(paper_ledger, intent_id=intent_id, symbol=thesis.symbol)
    if exit_event is None:
        return (item,)
    return (
        item,
        day_live_item(
            f"day.paper_exit.{thesis.symbol}",
            "paper",
            f"{thesis.symbol} Paper exit",
            "closed",
            exit_event.occurred_at,
            source,
        ),
    )


def render_us_day_corrupt_thesis_item(
    thesis: UsDayTradeThesis, now: dt.datetime
) -> CorruptDayThesisProjection:
    digest = hashlib.sha256(thesis.thesis_id.encode()).hexdigest()
    trace_id = f"trace.day.lifecycle.{digest[:32]}.corrupt"
    item = WorkspaceItemV2(
        item_id=f"day.lifecycle.{digest[:32]}.corrupt",
        kind="day_recommendation",
        label=redact_outbound_text(
            f"{thesis.symbol or thesis.theme_name} lifecycle corrupt", max_chars=80
        ),
        state="corrupt",
        value="paper lifecycle corrupt · no recommendation authority",
        observed_at=now,
        trace_id=trace_id,
    )
    blocker_id = f"{trace_id}.blocker"
    return CorruptDayThesisProjection(
        item,
        (
            day_live_node(trace_id, "source_receipt", "Day thesis lifecycle", now, digest, "unavailable"),
            day_live_node(blocker_id, "blocker_terminal", "Day thesis lifecycle blocked", now, digest, "blocked"),
        ),
        (TraceEdgeV2(from_node_id=trace_id, to_node_id=blocker_id, kind="blocked_by"),),
    )


def _terminal_thesis_item(
    thesis: UsDayTradeThesis,
    status: str,
    index: int,
    prefix: str,
    source: str,
) -> tuple[WorkspaceItemV2, ...]:
    match thesis.decision:
        case DayTradeDecision.NO_TRADE:
            value = f"NO_TRADE · {thesis.reason_code}"
        case DayTradeDecision.WATCH | DayTradeDecision.INSUFFICIENT_EVIDENCE:
            value = f"{thesis.decision.value.upper()} · {thesis.reason_code}"
        case DayTradeDecision.RECOMMEND:
            raise AssertionError
        case unreachable:
            assert_never(unreachable)
    return (
        day_live_item(
            f"{prefix}.{index}",
            "day_recommendation",
            f"Day terminal decision · {status}",
            value,
            thesis.observed_at,
            source,
        ),
    )


__all__ = (
    "CorruptDayThesisProjection",
    "render_us_day_corrupt_thesis_item",
    "render_us_day_paper_items",
    "render_us_day_thesis_items",
)
