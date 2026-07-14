from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from trading_agent.execution_schema import StoredBrokerEvent, StoredIntent
from trading_agent.paper_execution_models import (
    BrokerOrderEventType,
    BrokerOrderId,
    IntentId,
)
from trading_agent.trade_update_schema import StoredTradeUpdate

TERMINAL_EVENT_TYPES: Final = frozenset(
    (
        BrokerOrderEventType.FILL,
        BrokerOrderEventType.REJECTED,
        BrokerOrderEventType.CANCELED,
        BrokerOrderEventType.EXPIRED,
    )
)
EXECUTION_EVENT_TYPES: Final = frozenset(
    (
        BrokerOrderEventType.PARTIAL_FILL,
        BrokerOrderEventType.FILL,
    )
)


class StoredBrokerEventTimestampError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BrokerOrderLedgerState:
    intent_id: IntentId
    broker_order_ids: tuple[BrokerOrderId, ...]
    terminal_event_types: tuple[BrokerOrderEventType, ...]
    cumulative_filled_quantity: Decimal
    complete_fill: bool
    terminal: bool
    has_fill_evidence: bool
    anomaly_reasons: tuple[str, ...]


def project_broker_order_state(
    intent: StoredIntent,
    broker_events: tuple[StoredBrokerEvent, ...],
    trade_updates: tuple[StoredTradeUpdate, ...],
) -> BrokerOrderLedgerState:
    reasons: list[str] = []
    ordered_updates = tuple(
        sorted(trade_updates, key=lambda event: (_instant(event.occurred_at), event.event_id))
    )
    executions = tuple(
        event for event in ordered_updates if event.event_type in EXECUTION_EVENT_TYPES
    )
    cumulative = max(
        (event.cumulative_filled_quantity for event in trade_updates),
        default=Decimal(0),
    )
    expected_cumulative = Decimal(0)
    for execution in executions:
        if execution.execution_id is None:
            reasons.append("체결 event에 execution ID가 없습니다")
        if execution.execution_quantity is None:
            reasons.append("체결 event에 개별 체결 수량이 없습니다")
            continue
        expected_cumulative += execution.execution_quantity
        if execution.cumulative_filled_quantity != expected_cumulative:
            reasons.append("체결 event 사이에 누락 또는 누적 수량 불일치가 있습니다")
    if cumulative > 0 and expected_cumulative != cumulative:
        reasons.append("체결 event 누락 또는 최종 누적 수량 불일치가 있습니다")

    complete_fill = any(
        event.event_type is BrokerOrderEventType.FILL
        and event.cumulative_filled_quantity == Decimal(intent.quantity)
        for event in trade_updates
    )
    if any(
        event.event_type is BrokerOrderEventType.FILL for event in trade_updates
    ) and not complete_fill:
        reasons.append("완전체결 event의 누적 수량이 주문 수량과 다릅니다")

    legacy_fill = any(
        event.event_type in EXECUTION_EVENT_TYPES for event in broker_events
    )
    if legacy_fill:
        reasons.append("legacy 체결 event는 REST 재대사가 필요합니다")
    if any(
        event.event_type is not BrokerOrderEventType.SUBMITTED
        for event in broker_events
    ):
        reasons.append("legacy broker lifecycle event는 REST 재대사가 필요합니다")

    replacement = any(
        event.event_type is BrokerOrderEventType.REPLACED
        or event.replaced_by_order_id is not None
        or event.replaces_order_id is not None
        for event in trade_updates
    )
    if replacement:
        reasons.append("교체 주문은 REST 재대사가 필요합니다")

    terminal_types = tuple(
        sorted(
            {
                event.event_type
                for event in (*broker_events, *trade_updates)
                if event.event_type in TERMINAL_EVENT_TYPES
            },
            key=lambda event_type: event_type.value,
        )
    )
    if len(terminal_types) > 1:
        reasons.append("상호 배타적인 종료 event가 함께 존재하는 모순 이력입니다")
    has_authoritative_terminal = any(
        event.event_type in TERMINAL_EVENT_TYPES for event in trade_updates
    )
    broker_order_ids = tuple(
        sorted(
            {
                event.broker_order_id
                for event in (*broker_events, *trade_updates)
            }
        )
    )
    if len(broker_order_ids) > 1 and not replacement:
        reasons.append("연결 정보 없이 둘 이상의 broker order ID가 있습니다")
    _append_terminal_order_anomalies(ordered_updates, reasons)
    return BrokerOrderLedgerState(
        intent_id=intent.intent_id,
        broker_order_ids=broker_order_ids,
        terminal_event_types=terminal_types,
        cumulative_filled_quantity=cumulative,
        complete_fill=complete_fill,
        terminal=has_authoritative_terminal,
        has_fill_evidence=cumulative > 0 or legacy_fill,
        anomaly_reasons=tuple(sorted(set(reasons))),
    )


def _append_terminal_order_anomalies(
    updates: tuple[StoredTradeUpdate, ...],
    reasons: list[str],
) -> None:
    terminal_times = tuple(
        _instant(event.occurred_at)
        for event in updates
        if event.event_type in TERMINAL_EVENT_TYPES
    )
    if not terminal_times:
        return
    terminal_at = min(terminal_times)
    invalid_after_terminal = frozenset(
        (
            BrokerOrderEventType.NEW,
            BrokerOrderEventType.ACCEPTED,
            BrokerOrderEventType.PENDING_NEW,
            BrokerOrderEventType.STOPPED,
            BrokerOrderEventType.PARTIAL_FILL,
            BrokerOrderEventType.PENDING_CANCEL,
            BrokerOrderEventType.PENDING_REPLACE,
        )
    )
    if any(
        event.event_type in invalid_after_terminal
        and _instant(event.occurred_at) > terminal_at
        for event in updates
    ):
        reasons.append("종료 event 뒤에 더 늦은 비종료 event가 있습니다")


def _instant(value: str) -> dt.datetime:
    instant = dt.datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise StoredBrokerEventTimestampError
    return instant
