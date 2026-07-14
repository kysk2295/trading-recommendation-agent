from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from trading_agent.broker_execution_projection import project_broker_execution
from trading_agent.broker_order_evidence import BrokerOrderEvidence
from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_execution_models import (
    BrokerOrderEventType,
    BrokerOrderId,
    IntentId,
)
from trading_agent.rest_recovery_projection import (
    latest_recovery_order,
    recovery_has_replacement,
    recovery_order_integrity_reasons,
    recovery_order_mismatch_reasons,
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
REST_TERMINAL_STATUS_TO_EVENT: Final = {
    "filled": BrokerOrderEventType.FILL,
    "canceled": BrokerOrderEventType.CANCELED,
    "expired": BrokerOrderEventType.EXPIRED,
    "rejected": BrokerOrderEventType.REJECTED,
}


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
    execution_detail_complete: bool = True
    warning_reasons: tuple[str, ...] = ()
    execution_average_price: Decimal | None = None


def project_broker_order_state(
    intent: StoredIntent,
    evidence: BrokerOrderEvidence,
) -> BrokerOrderLedgerState:
    reasons: list[str] = []
    warnings: list[str] = []
    latest_recovery, recovery_reasons = latest_recovery_order(evidence.recovery_orders)
    reasons.extend(recovery_reasons)
    execution = project_broker_execution(intent, evidence, latest_recovery)
    reasons.extend(execution.reasons)
    warnings.extend(execution.warnings)
    if latest_recovery is not None:
        recovered = latest_recovery.order
        reasons.extend(recovery_order_mismatch_reasons(intent, recovered))
        reasons.extend(recovery_order_integrity_reasons(recovered))

    legacy_fill = any(
        event.event_type
        in (
            BrokerOrderEventType.PARTIAL_FILL,
            BrokerOrderEventType.FILL,
        )
        for event in evidence.broker_events
    )
    if legacy_fill:
        reasons.append("legacy 체결 event는 REST 재대사가 필요합니다")
    if any(event.event_type is not BrokerOrderEventType.SUBMITTED for event in evidence.broker_events):
        reasons.append("legacy broker lifecycle event는 REST 재대사가 필요합니다")

    replacement = any(
        event.event_type is BrokerOrderEventType.REPLACED
        or event.replaced_by_order_id is not None
        or event.replaces_order_id is not None
        for event in evidence.trade_updates
    ) or recovery_has_replacement(evidence.recovery_orders)
    if replacement:
        reasons.append("교체 주문은 REST 재대사가 필요합니다")

    terminal_type_set = {
        event.event_type
        for event in (*evidence.broker_events, *evidence.trade_updates)
        if event.event_type in TERMINAL_EVENT_TYPES
    }
    if latest_recovery is not None and latest_recovery.order.status in REST_TERMINAL_STATUS_TO_EVENT:
        terminal_type_set.add(REST_TERMINAL_STATUS_TO_EVENT[latest_recovery.order.status])
    terminal_types = tuple(sorted(terminal_type_set, key=lambda event_type: event_type.value))
    if len(terminal_types) > 1:
        reasons.append("상호 배타적인 종료 event가 함께 존재하는 모순 이력입니다")
    has_authoritative_terminal = any(event.event_type in TERMINAL_EVENT_TYPES for event in evidence.trade_updates) or (
        latest_recovery is not None and latest_recovery.order.status in REST_TERMINAL_STATUS_TO_EVENT
    )
    broker_order_ids = tuple(
        sorted(
            {event.broker_order_id for event in (*evidence.broker_events, *evidence.trade_updates)}
            | {recovery.order.broker_order_id for recovery in evidence.recovery_orders}
        )
    )
    if len(broker_order_ids) > 1 and not replacement:
        reasons.append("연결 정보 없이 둘 이상의 broker order ID가 있습니다")
    _append_terminal_order_anomalies(execution.ordered_updates, reasons)
    return BrokerOrderLedgerState(
        intent_id=intent.intent_id,
        broker_order_ids=broker_order_ids,
        terminal_event_types=terminal_types,
        cumulative_filled_quantity=execution.cumulative_quantity,
        complete_fill=execution.complete_fill,
        terminal=has_authoritative_terminal,
        has_fill_evidence=execution.cumulative_quantity > 0 or legacy_fill,
        anomaly_reasons=tuple(sorted(set(reasons))),
        execution_detail_complete=execution.detail_complete,
        warning_reasons=tuple(sorted(set(warnings))),
        execution_average_price=execution.average_price,
    )


def _append_terminal_order_anomalies(
    updates: tuple[StoredTradeUpdate, ...],
    reasons: list[str],
) -> None:
    terminal_times = tuple(_instant(event.occurred_at) for event in updates if event.event_type in TERMINAL_EVENT_TYPES)
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
        event.event_type in invalid_after_terminal and _instant(event.occurred_at) > terminal_at for event in updates
    ):
        reasons.append("종료 event 뒤에 더 늦은 비종료 event가 있습니다")


def _instant(value: str) -> dt.datetime:
    instant = dt.datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise StoredBrokerEventTimestampError
    return instant
