from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from typing import override

from trading_agent.alpaca_paper_client import AlpacaPaperClient
from trading_agent.alpaca_paper_config import (
    AlpacaPaperCredentials,
    create_alpaca_paper_read_client,
)
from trading_agent.alpaca_paper_order_stream import PaperOrderStreamHeartbeat
from trading_agent.execution_errors import AccountBindingConflictError
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_execution_models import (
    IntentId,
    PaperBrokerState,
    PaperOrderSnapshot,
)
from trading_agent.paper_runtime import (
    MAX_RUNTIME_RECEIPT_AGE,
    PaperRuntimeEpochChangedError,
)
from trading_agent.paper_stream_recovery import (
    PaperRecoveryState,
    PaperStreamRecoveryObservation,
)
from trading_agent.paper_stream_recovery_snapshot import (
    execution_details_are_complete,
    recovery_order_observations,
    recovery_snapshot_json,
)

RECOVERY_ORDER_LOOKBACK = dt.timedelta(days=7)


class PaperStreamRecoveryIncompleteError(RuntimeError):
    __slots__ = ("reasons",)

    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__()
        self.reasons = reasons

    @override
    def __str__(self) -> str:
        return "Alpaca paper REST 복구가 불완전합니다: " + ", ".join(self.reasons)


type PaperRecoveryStateLoader = Callable[
    [AlpacaPaperCredentials, frozenset[IntentId]],
    PaperRecoveryState,
]


def read_paper_recovery_state(
    credentials: AlpacaPaperCredentials,
    unresolved_intent_ids: frozenset[IntentId],
) -> PaperRecoveryState:
    with create_alpaca_paper_read_client() as http_client:
        client = AlpacaPaperClient(http_client, credentials)
        account = client.account()
        open_orders = client.open_orders()
        open_ids = frozenset(order.client_order_id for order in open_orders)
        targeted: list[PaperOrderSnapshot] = []
        missing: list[str] = []
        for intent_id in sorted(unresolved_intent_ids - open_ids):
            order = client.order_by_client_id(intent_id)
            if order is None:
                missing.append(intent_id)
            else:
                targeted.append(order)
        if missing:
            raise PaperStreamRecoveryIncompleteError(
                tuple(f"미해결 intent REST 조회 404: {intent_id}" for intent_id in missing)
            )
        known_order_ids = frozenset(
            order.broker_order_id for order in (*open_orders, *targeted)
        )
        recent_orders = tuple(
            order
            for order in client.recent_orders(
                account.observed_at - RECOVERY_ORDER_LOOKBACK
            )
            if order.broker_order_id not in known_order_ids
        )
        return PaperRecoveryState(
            PaperBrokerState(account, open_orders, client.positions()),
            tuple(targeted),
            recent_orders,
        )


def build_paper_stream_recovery_observation(
    before_rest: PaperOrderStreamHeartbeat,
    after_rest: PaperOrderStreamHeartbeat,
    state: PaperRecoveryState,
    ledger: ReconciliationLedger,
) -> PaperStreamRecoveryObservation:
    if before_rest.connection_epoch != after_rest.connection_epoch:
        raise PaperRuntimeEpochChangedError
    broker_state = state.broker_state
    if (
        ledger.account_fingerprint is not None
        and ledger.account_fingerprint
        != broker_state.account.account_fingerprint
    ):
        raise AccountBindingConflictError
    reasons = _recovery_reasons(before_rest, after_rest, state, ledger)
    if reasons:
        raise PaperStreamRecoveryIncompleteError(reasons)
    return PaperStreamRecoveryObservation(
        account_fingerprint=broker_state.account.account_fingerprint,
        connection_epoch=before_rest.connection_epoch,
        started_at=before_rest.pong_at,
        completed_at=after_rest.pong_at,
        snapshot_json=recovery_snapshot_json(state, ledger.unresolved_intent_ids),
        execution_detail_complete=execution_details_are_complete(state, ledger),
        orders=recovery_order_observations(state),
    )


def _recovery_reasons(
    before: PaperOrderStreamHeartbeat,
    after: PaperOrderStreamHeartbeat,
    state: PaperRecoveryState,
    ledger: ReconciliationLedger,
) -> tuple[str, ...]:
    reasons: list[str] = []
    observed_at = state.broker_state.account.observed_at
    if (
        before.pong_at >= after.pong_at
        or not before.pong_at <= observed_at <= after.pong_at
        or after.pong_at - observed_at > MAX_RUNTIME_RECEIPT_AGE
    ):
        reasons.append("REST 계좌 수신시각이 heartbeat 복구 구간 밖입니다")
    all_orders = (*state.broker_state.open_orders, *state.targeted_orders)
    all_recovery_orders = (*all_orders, *state.recent_orders)
    broker_order_ids = tuple(order.broker_order_id for order in all_recovery_orders)
    if len(broker_order_ids) != len(set(broker_order_ids)):
        reasons.append("REST 복구에 중복 broker order ID가 있습니다")
    counts: dict[IntentId, int] = {}
    for order in all_orders:
        counts[order.client_order_id] = counts.get(order.client_order_id, 0) + 1
    missing = ledger.unresolved_intent_ids - counts.keys()
    reasons.extend(f"미해결 intent REST 주문 누락: {intent_id}" for intent_id in sorted(missing))
    unexpected_targeted = frozenset(
        order.client_order_id for order in state.targeted_orders
    ) - ledger.unresolved_intent_ids
    reasons.extend(
        f"요청하지 않은 targeted REST 주문: {intent_id}"
        for intent_id in sorted(unexpected_targeted)
    )
    reasons.extend(
        f"REST 복구에 중복 client order ID가 있습니다: {intent_id}"
        for intent_id, count in sorted(counts.items())
        if count > 1
    )
    intents = {intent.intent_id: intent for intent in ledger.intents}
    for source, orders in (
        ("open", state.broker_state.open_orders),
        ("targeted", state.targeted_orders),
        ("recent", state.recent_orders),
    ):
        reasons.extend(
            f"알 수 없는 {source} REST 주문: {order.client_order_id}"
            for order in orders
            if order.client_order_id not in intents
        )
    for order in (*all_orders, *state.recent_orders):
        intent = intents.get(order.client_order_id)
        if intent is not None:
            reasons.extend(_order_reasons(intent, order))
    return tuple(sorted(set(reasons)))


def _order_reasons(
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
    return tuple(
        f"REST 복구 주문 불일치: {intent.intent_id} ({field})"
        for field in mismatches
    )
