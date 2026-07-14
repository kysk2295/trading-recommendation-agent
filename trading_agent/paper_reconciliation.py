from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_agent.broker_order_projection import BrokerOrderLedgerState
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
    order_states: tuple[BrokerOrderLedgerState, ...] = ()


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    ready: bool
    reasons: tuple[str, ...]


def reconcile_paper_state(
    snapshot: PaperReconciliationSnapshot,
) -> ReconciliationResult:
    return _reconcile_paper_state(snapshot, reject_open_positions=True)


def reconcile_operational_paper_state(
    snapshot: PaperReconciliationSnapshot,
) -> ReconciliationResult:
    return _reconcile_paper_state(snapshot, reject_open_positions=False)


def _reconcile_paper_state(
    snapshot: PaperReconciliationSnapshot,
    *,
    reject_open_positions: bool,
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
    states_by_id = {state.intent_id: state for state in snapshot.order_states}
    if len(states_by_id) != len(snapshot.order_states):
        reasons.append("원장에 중복된 주문 projection이 있습니다")
    for state in snapshot.order_states:
        reasons.extend(
            f"주문 projection 이상: {state.intent_id} ({reason})"
            for reason in state.anomaly_reasons
        )
    open_order_ids: set[IntentId] = set()
    open_order_counts: dict[IntentId, int] = {}
    for order in snapshot.broker_orders:
        open_order_ids.add(order.client_order_id)
        open_order_counts[order.client_order_id] = (
            open_order_counts.get(order.client_order_id, 0) + 1
        )
        intent = intents_by_id.get(order.client_order_id)
        if intent is None:
            reasons.append(f"알 수 없는 paper 주문: {order.client_order_id}")
            continue
        if order.client_order_id not in snapshot.unresolved_intent_ids:
            reasons.append(
                f"종료된 local intent에 열린 broker 주문이 있습니다: {order.client_order_id}"
            )
        mismatches = _order_mismatches(intent, order)
        if mismatches:
            reasons.append(
                f"paper 주문 불일치: {order.client_order_id} ({', '.join(mismatches)})"
            )
        state = states_by_id.get(order.client_order_id)
        if state is not None:
            if (
                state.broker_order_ids
                and order.broker_order_id not in state.broker_order_ids
            ):
                reasons.append(
                    f"paper 주문 ID가 원장 projection과 다릅니다: {order.client_order_id}"
                )
            if order.filled_quantity != state.cumulative_filled_quantity:
                reasons.append(
                    f"paper 체결 수량이 원장 projection과 다릅니다: {order.client_order_id}"
                )

    for intent_id, count in open_order_counts.items():
        if count > 1:
            reasons.append(f"하나의 intent에 둘 이상의 broker 주문이 있습니다: {intent_id}")

    for intent_id in sorted(snapshot.unresolved_intent_ids):
        if intent_id not in open_order_ids:
            reasons.append(f"미해결 local intent의 broker 주문이 없습니다: {intent_id}")
    if reject_open_positions:
        for position in snapshot.positions:
            if position.quantity != 0:
                reasons.append(
                    f"열린 paper 포지션: {position.symbol} ({position.quantity})"
                )

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
