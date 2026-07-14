from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    IntentId,
    PaperAccountSnapshot,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)


@dataclass(frozen=True, slots=True)
class PaperReconciliationSnapshot:
    account: PaperAccountSnapshot
    broker_orders: tuple[PaperOrderSnapshot, ...]
    positions: tuple[PaperPositionSnapshot, ...]
    stored_intents: tuple[StoredIntent, ...]
    unresolved_intent_ids: frozenset[IntentId]
    bound_account_fingerprint: AccountFingerprint | None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    ready: bool
    reasons: tuple[str, ...]


def reconcile_paper_state(
    snapshot: PaperReconciliationSnapshot,
) -> ReconciliationResult:
    reasons: list[str] = []
    if snapshot.account.trading_blocked:
        reasons.append("Alpaca paper 계좌가 거래 차단 상태입니다")
    if snapshot.account.status != "ACTIVE":
        reasons.append(
            f"Alpaca paper 계좌 상태가 ACTIVE가 아닙니다: {snapshot.account.status}"
        )
    if snapshot.bound_account_fingerprint is None:
        reasons.append("실행 원장이 Alpaca paper 계좌에 결합되지 않았습니다")
    elif snapshot.bound_account_fingerprint != snapshot.account.account_fingerprint:
        reasons.append("Alpaca paper 계좌 fingerprint가 실행 원장과 다릅니다")

    intents_by_id = {intent.intent_id: intent for intent in snapshot.stored_intents}
    open_order_ids: set[IntentId] = set()
    for order in snapshot.broker_orders:
        open_order_ids.add(order.client_order_id)
        intent = intents_by_id.get(order.client_order_id)
        if intent is None:
            reasons.append(f"알 수 없는 paper 주문: {order.client_order_id}")
            continue
        mismatches = _order_mismatches(intent, order)
        if mismatches:
            reasons.append(
                f"paper 주문 불일치: {order.client_order_id} ({', '.join(mismatches)})"
            )

    for intent_id in sorted(snapshot.unresolved_intent_ids):
        if intent_id not in open_order_ids:
            reasons.append(f"미해결 local intent의 broker 주문이 없습니다: {intent_id}")
    for position in snapshot.positions:
        if position.quantity != 0:
            reasons.append(f"열린 paper 포지션: {position.symbol} ({position.quantity})")

    ordered_reasons = tuple(sorted(reasons))
    return ReconciliationResult(ready=not ordered_reasons, reasons=ordered_reasons)


def _order_mismatches(
    intent: StoredIntent,
    order: PaperOrderSnapshot,
) -> tuple[str, ...]:
    mismatches: list[str] = []
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
    return tuple(mismatches)
