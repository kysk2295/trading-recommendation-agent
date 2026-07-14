from __future__ import annotations

import datetime as dt
from decimal import Decimal

from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderId,
    IntentId,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperOrderSide,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.paper_risk import PaperRiskConfig
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperClosePositionAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
)
from trading_agent.paper_safety_mutation_scope import paper_safety_mutation_scope_reasons

NOW = dt.datetime(2026, 7, 14, 19, 55, tzinfo=dt.UTC)
SMOKE_CONFIG = PaperRiskConfig(
    max_risk_dollars=10.0,
    risk_fraction=0.0003333333333333333,
    max_notional_dollars=100.0,
    max_open_positions=1,
    daily_loss_limit_dollars=30.0,
    per_side_cost_bps=20.0,
)


def test_scope_allows_one_partial_entry_and_position_within_total_notional() -> None:
    order = _order("order-1", "intent-1", "AAA", quantity="10", filled="5", price="10")
    position = PaperPositionSnapshot("AAA", Decimal("5"), Decimal("50"))
    state = _state((order,), (position,))
    plan = _plan(
        (
            PaperCancelOrderAction(order.broker_order_id, "AAA", False),
            PaperClosePositionAction("AAA", PaperOrderSide.SELL, Decimal("5")),
        )
    )

    assert paper_safety_mutation_scope_reasons(state, plan, SMOKE_CONFIG) == ()


def test_scope_blocks_more_entry_orders_and_symbols_than_smoke_limit() -> None:
    first = _order("order-1", "intent-1", "AAA", quantity="5", price="10")
    second = _order("order-2", "intent-2", "BBB", quantity="5", price="10")
    state = _state((first, second), ())
    plan = _plan(
        (
            PaperCancelOrderAction(first.broker_order_id, "AAA", False),
            PaperCancelOrderAction(second.broker_order_id, "BBB", False),
        )
    )

    reasons = paper_safety_mutation_scope_reasons(state, plan, SMOKE_CONFIG)

    assert any("entry order" in reason for reason in reasons)
    assert any("symbol" in reason for reason in reasons)


def test_scope_blocks_position_notional_above_smoke_limit() -> None:
    position = PaperPositionSnapshot("AAA", Decimal("10"), Decimal("100.01"))
    state = _state((), (position,))
    plan = _plan((PaperClosePositionAction("AAA", PaperOrderSide.SELL, Decimal("10")),))

    reasons = paper_safety_mutation_scope_reasons(state, plan, SMOKE_CONFIG)

    assert any("notional" in reason for reason in reasons)


def test_scope_blocks_pending_entry_without_finite_positive_limit_price() -> None:
    order = _order("order-1", "intent-1", "AAA", quantity="5", price=None)
    state = _state((order,), ())
    plan = _plan((PaperCancelOrderAction(order.broker_order_id, "AAA", False),))

    reasons = paper_safety_mutation_scope_reasons(state, plan, SMOKE_CONFIG)

    assert any("entry notional" in reason for reason in reasons)


def _state(
    orders: tuple[PaperOrderSnapshot, ...],
    positions: tuple[PaperPositionSnapshot, ...],
) -> PaperBrokerState:
    account = PaperAccountSnapshot(
        NOW,
        "ACTIVE",
        False,
        Decimal("30000"),
        Decimal("30000"),
        Decimal("60000"),
        AccountFingerprint("f" * 64),
    )
    return PaperBrokerState(account, orders, positions)


def _order(
    broker_order_id: str,
    intent_id: str,
    symbol: str,
    *,
    quantity: str,
    filled: str = "0",
    price: str | None,
) -> PaperOrderSnapshot:
    return PaperOrderSnapshot(
        BrokerOrderId(broker_order_id),
        IntentId(intent_id),
        symbol,
        PaperOrderSide.BUY,
        "accepted",
        Decimal(quantity),
        Decimal(filled),
        None if price is None else Decimal(price),
        "day",
        False,
    )


def _plan(
    actions: tuple[PaperCancelOrderAction | PaperClosePositionAction, ...],
) -> PaperSafetyPlan:
    return PaperSafetyPlan(
        AccountFingerprint("f" * 64),
        NOW,
        dt.date(2026, 7, 14),
        PaperSafetyPhase.EOD_FLATTEN,
        Decimal("0"),
        Decimal("0"),
        actions,
    )
