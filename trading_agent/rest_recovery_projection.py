from __future__ import annotations

from decimal import Decimal

from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_execution_models import BrokerOrderId, PaperOrderSnapshot
from trading_agent.paper_stream_recovery import StoredPaperRecoveryOrder

REST_TERMINAL_STATUSES = frozenset(("filled", "canceled", "expired", "rejected"))


def latest_recovery_order(
    recoveries: tuple[StoredPaperRecoveryOrder, ...],
) -> tuple[StoredPaperRecoveryOrder | None, tuple[str, ...]]:
    reasons: list[str] = []
    latest_by_order_id: dict[BrokerOrderId, StoredPaperRecoveryOrder] = {}
    for recovery in sorted(recoveries, key=lambda item: item.recovery_id):
        prior = latest_by_order_id.get(recovery.order.broker_order_id)
        if (
            prior is not None
            and recovery.order.filled_quantity < prior.order.filled_quantity
        ):
            reasons.append("REST 복구 사이 누적 체결 수량이 감소했습니다")
        if (
            prior is not None
            and prior.order.status in REST_TERMINAL_STATUSES
            and recovery.order.status != prior.order.status
        ):
            reasons.append("REST 복구 뒤 종료 상태가 변경되었습니다")
        if (
            prior is not None
            and prior.order.status in REST_TERMINAL_STATUSES
            and recovery.order.filled_quantity != prior.order.filled_quantity
        ):
            reasons.append("REST 종료 상태의 누적 체결 수량이 변경되었습니다")
        if (
            prior is not None
            and prior.order.status in REST_TERMINAL_STATUSES
            and recovery.order.filled_average_price
            != prior.order.filled_average_price
        ):
            reasons.append("REST 종료 상태의 누적 체결 평균가격이 변경되었습니다")
        latest_by_order_id[recovery.order.broker_order_id] = recovery
    if not latest_by_order_id:
        return None, tuple(reasons)
    if len(latest_by_order_id) > 1:
        reasons.append("REST 복구에 교체 또는 복수 broker 주문이 있습니다")
    latest = max(latest_by_order_id.values(), key=lambda item: item.recovery_id)
    return latest, tuple(reasons)


def recovery_order_mismatch_reasons(
    intent: StoredIntent,
    order: PaperOrderSnapshot,
) -> tuple[str, ...]:
    mismatches: list[str] = []
    if order.client_order_id != intent.intent_id:
        mismatches.append("client_order_id")
    if order.symbol != intent.symbol:
        mismatches.append("symbol")
    if order.side != intent.side:
        mismatches.append("side")
    if order.quantity != Decimal(intent.quantity):
        mismatches.append("quantity")
    if order.limit_price != intent.entry_limit:
        mismatches.append("limit_price")
    if order.time_in_force != "day":
        mismatches.append("time_in_force")
    if order.extended_hours:
        mismatches.append("extended_hours")
    return tuple(f"REST 복구 주문 불일치: {field}" for field in mismatches)


def recovery_order_integrity_reasons(
    order: PaperOrderSnapshot,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if (
        not order.quantity.is_finite()
        or order.quantity <= 0
        or not order.filled_quantity.is_finite()
        or order.filled_quantity < 0
        or order.filled_quantity > order.quantity
    ):
        reasons.append("REST 주문 수량 범위가 올바르지 않습니다")
    if order.status == "filled" and order.filled_quantity != order.quantity:
        reasons.append("REST filled 상태의 누적 수량이 주문 수량과 다릅니다")
    if order.status == "partially_filled" and not (
        Decimal(0) < order.filled_quantity < order.quantity
    ):
        reasons.append("REST partially_filled 상태의 누적 수량이 올바르지 않습니다")
    if order.filled_quantity > 0 and (
        order.filled_average_price is None
        or not order.filled_average_price.is_finite()
        or order.filled_average_price <= 0
    ):
        reasons.append("REST 누적 체결 평균가격이 올바르지 않습니다")
    return tuple(reasons)


def recovery_has_replacement(
    recoveries: tuple[StoredPaperRecoveryOrder, ...],
) -> bool:
    return any(
        recovery.order.replaced_by_order_id is not None
        or recovery.order.replaces_order_id is not None
        or recovery.order.status == "replaced"
        for recovery in recoveries
    )
