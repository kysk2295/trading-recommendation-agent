from __future__ import annotations

from typing import assert_never

from trading_agent.dashboard_models_v2 import WorkspaceItemV2
from trading_agent.dashboard_us_day_live_primitives import day_live_item
from trading_agent.dashboard_us_day_paper import FinalizedPaperProjectionBundle, canonical_day_exit_event
from trading_agent.models import RecommendationEvent
from trading_agent.paper_execution_models import IntentId
from trading_agent.us_day_lifecycle import derive_us_day_lifecycle
from trading_agent.us_day_thesis_models import DayTradeDecision, UsDayThesisChange, UsDayTradeThesis


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


__all__ = ("render_us_day_paper_items", "render_us_day_thesis_items")
