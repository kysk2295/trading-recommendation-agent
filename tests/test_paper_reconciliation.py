from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from typing import Final

import pytest

from trading_agent.broker_order_projection import BrokerOrderLedgerState
from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderId,
    IntentId,
    PaperAccountSnapshot,
    PaperOrderSide,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.paper_reconciliation import (
    PaperReconciliationSnapshot,
    reconcile_operational_paper_state,
    reconcile_paper_state,
)

FINGERPRINT = AccountFingerprint("a" * 64)
KNOWN_INTENT_ID: Final = IntentId("known-intent")


def _account(
    *,
    blocked: bool = False,
    fingerprint: AccountFingerprint = FINGERPRINT,
) -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        observed_at=dt.datetime(2026, 7, 14, 13, 25, tzinfo=dt.UTC),
        status="ACTIVE",
        trading_blocked=blocked,
        equity=Decimal("30000"),
        last_equity=Decimal("30000"),
        buying_power=Decimal("12000"),
        account_fingerprint=fingerprint,
    )


def _stored_intent() -> StoredIntent:
    return StoredIntent(
        intent_id=KNOWN_INTENT_ID,
        strategy_id="orb",
        strategy_version="1.0.0",
        symbol="AAA",
        created_at="2026-07-14T09:36:00-04:00",
        side=PaperOrderSide.BUY,
        entry_limit=Decimal("10.0"),
        stop=Decimal("9.75"),
        target_1r=Decimal("10.25"),
        target_2r=Decimal("10.50"),
        quantity=259,
    )


def _order(client_order_id: IntentId = KNOWN_INTENT_ID) -> PaperOrderSnapshot:
    return PaperOrderSnapshot(
        broker_order_id=BrokerOrderId("paper-order-1"),
        client_order_id=client_order_id,
        symbol="AAA",
        side=PaperOrderSide.BUY,
        status="accepted",
        quantity=Decimal("259"),
        filled_quantity=Decimal("0"),
        limit_price=Decimal("10.0"),
        time_in_force="day",
        extended_hours=False,
    )


def _snapshot(
    *,
    orders: tuple[PaperOrderSnapshot, ...] = (),
    positions: tuple[PaperPositionSnapshot, ...] = (),
    intents: tuple[StoredIntent, ...] = (),
    unresolved: frozenset[IntentId] = frozenset(),
    order_states: tuple[BrokerOrderLedgerState, ...] = (),
    bound_fingerprint: AccountFingerprint | None = FINGERPRINT,
    account: PaperAccountSnapshot | None = None,
) -> PaperReconciliationSnapshot:
    return PaperReconciliationSnapshot(
        account=_account() if account is None else account,
        broker_orders=orders,
        positions=positions,
        stored_intents=intents,
        unresolved_intent_ids=unresolved,
        bound_account_fingerprint=bound_fingerprint,
        order_states=order_states,
    )


def test_empty_bound_ledger_and_broker_state_is_ready() -> None:
    # Given
    snapshot = _snapshot()

    # When
    result = reconcile_paper_state(snapshot)

    # Then
    assert result.ready is True
    assert result.reasons == ()


def test_unbound_or_different_account_blocks_readiness() -> None:
    # Given
    unbound = _snapshot(bound_fingerprint=None)
    switched = _snapshot(
        account=_account(fingerprint=AccountFingerprint("b" * 64))
    )

    # When
    unbound_result = reconcile_paper_state(unbound)
    switched_result = reconcile_paper_state(switched)

    # Then
    assert unbound_result.ready is False
    assert "결합되지 않았습니다" in unbound_result.reasons[0]
    assert switched_result.ready is False
    assert "fingerprint" in switched_result.reasons[0]


def test_exact_known_open_order_is_ready() -> None:
    # Given
    intent = _stored_intent()
    snapshot = _snapshot(
        orders=(_order(),),
        intents=(intent,),
        unresolved=frozenset({intent.intent_id}),
    )

    # When
    result = reconcile_paper_state(snapshot)

    # Then
    assert result.ready is True


def test_open_order_for_a_locally_terminal_intent_is_blocked() -> None:
    intent = _stored_intent()
    snapshot = _snapshot(
        orders=(_order(),),
        intents=(intent,),
        unresolved=frozenset(),
    )

    result = reconcile_operational_paper_state(snapshot)

    assert result.ready is False
    assert any("종료된 local intent" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    "changed_order",
    (
        replace(_order(), symbol="BBB"),
        replace(_order(), side=PaperOrderSide.SELL),
        replace(_order(), quantity=Decimal("258")),
        replace(_order(), limit_price=Decimal("10.01")),
        replace(_order(), time_in_force="gtc"),
        replace(_order(), extended_hours=True),
    ),
)
def test_known_client_id_with_different_order_fields_is_blocked(
    changed_order: PaperOrderSnapshot,
) -> None:
    # Given
    intent = _stored_intent()
    snapshot = _snapshot(
        orders=(changed_order,),
        intents=(intent,),
        unresolved=frozenset({intent.intent_id}),
    )

    # When
    result = reconcile_paper_state(snapshot)

    # Then
    assert result.ready is False
    assert "paper 주문 불일치" in result.reasons[0]


def test_unknown_order_missing_unresolved_intent_and_fractional_position_block() -> None:
    # Given
    intent = _stored_intent()
    unknown = _order(IntentId("unknown-intent"))
    position = PaperPositionSnapshot("AAA", Decimal("0.5"), Decimal("5.0"))
    snapshot = _snapshot(
        orders=(unknown,),
        positions=(position,),
        intents=(intent,),
        unresolved=frozenset({intent.intent_id}),
    )

    # When
    result = reconcile_paper_state(snapshot)

    # Then
    assert result.ready is False
    assert any("알 수 없는 paper 주문" in reason for reason in result.reasons)
    assert any("broker 주문이 없습니다" in reason for reason in result.reasons)
    assert any("열린 paper 포지션" in reason for reason in result.reasons)


def test_blocked_or_nonactive_account_blocks_readiness() -> None:
    # Given
    blocked = _snapshot(account=_account(blocked=True))
    inactive = _snapshot(account=replace(_account(), status="INACTIVE"))

    # When
    blocked_result = reconcile_paper_state(blocked)
    inactive_result = reconcile_paper_state(inactive)

    # Then
    assert blocked_result.ready is False
    assert "거래 차단" in blocked_result.reasons[0]
    assert inactive_result.ready is False
    assert "ACTIVE" in inactive_result.reasons[0]


def test_operational_reconciliation_delegates_position_join_to_portfolio_builder() -> None:
    position = PaperPositionSnapshot("AAA", Decimal("259"), Decimal("2600"))
    snapshot = _snapshot(positions=(position,), intents=(_stored_intent(),))

    preflight = reconcile_paper_state(snapshot)
    operational = reconcile_operational_paper_state(snapshot)

    assert preflight.ready is False
    assert operational.ready is True


def test_projected_ledger_anomaly_blocks_reconciliation() -> None:
    intent = _stored_intent()
    state = BrokerOrderLedgerState(
        intent_id=intent.intent_id,
        broker_order_ids=(BrokerOrderId("paper-order-1"),),
        terminal_event_types=(),
        cumulative_filled_quantity=Decimal("10"),
        complete_fill=False,
        terminal=False,
        has_fill_evidence=True,
        anomaly_reasons=("체결 event 사이에 누락이 있습니다",),
    )
    snapshot = _snapshot(
        orders=(replace(_order(), filled_quantity=Decimal("10")),),
        intents=(intent,),
        unresolved=frozenset({intent.intent_id}),
        order_states=(state,),
    )

    result = reconcile_operational_paper_state(snapshot)

    assert result.ready is False
    assert any("누락" in reason for reason in result.reasons)


def test_duplicate_broker_orders_for_one_client_id_are_blocked() -> None:
    intent = _stored_intent()
    snapshot = _snapshot(
        orders=(
            _order(),
            replace(_order(), broker_order_id=BrokerOrderId("paper-order-2")),
        ),
        intents=(intent,),
        unresolved=frozenset({intent.intent_id}),
    )

    result = reconcile_operational_paper_state(snapshot)

    assert result.ready is False
    assert any("둘 이상의 broker 주문" in reason for reason in result.reasons)
