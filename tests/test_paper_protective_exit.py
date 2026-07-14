from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from tests.paper_runtime_fixtures import EXISTING_ID
from trading_agent.broker_order_projection import BrokerOrderLedgerState
from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_execution_models import (
    BrokerOrderEventType,
    BrokerOrderId,
    PaperOrderSide,
    PaperPositionSnapshot,
)
from trading_agent.paper_protective_exit import (
    BlockedProtectiveExitPlan,
    ProtectiveOcoExitPlan,
    plan_protective_oco_exit,
)


def _intent(side: PaperOrderSide = PaperOrderSide.BUY) -> StoredIntent:
    return StoredIntent(
        intent_id=EXISTING_ID,
        strategy_id="orb",
        strategy_version="1",
        symbol="MSFT",
        created_at="2026-07-14T09:35:00-04:00",
        side=side,
        entry_limit=Decimal("100"),
        stop=Decimal("99") if side is PaperOrderSide.BUY else Decimal("101"),
        target_1r=Decimal("101") if side is PaperOrderSide.BUY else Decimal("99"),
        target_2r=Decimal("102") if side is PaperOrderSide.BUY else Decimal("98"),
        quantity=50,
    )


def _partial_state(quantity: str = "20") -> BrokerOrderLedgerState:
    return BrokerOrderLedgerState(
        intent_id=EXISTING_ID,
        broker_order_ids=(BrokerOrderId("paper-entry-1"),),
        terminal_event_types=(BrokerOrderEventType.PARTIAL_FILL,),
        cumulative_filled_quantity=Decimal(quantity),
        complete_fill=False,
        terminal=False,
        has_fill_evidence=True,
        anomaly_reasons=(),
        execution_detail_complete=True,
        execution_average_price=Decimal("100.05"),
    )


def test_partial_long_fill_plans_one_day_oco_for_the_exact_position() -> None:
    # Given: a verified 20-share long partial fill and matching broker position.
    position = PaperPositionSnapshot("MSFT", Decimal(20), Decimal("2001"))

    # When: the protective exit is planned before another entry can be admitted.
    decision = plan_protective_oco_exit(_intent(), _partial_state(), position)

    # Then: one deterministic DAY OCO protects exactly the filled shares at stop and 2R.
    assert isinstance(decision, ProtectiveOcoExitPlan)
    assert decision.side is PaperOrderSide.SELL
    assert decision.quantity == 20
    assert decision.take_profit_limit == Decimal("102")
    assert decision.stop_price == Decimal("99")
    assert decision.order_class == "oco"
    assert decision.order_type == "limit"
    assert decision.time_in_force == "day"
    assert decision.extended_hours is False
    assert len(decision.client_order_id) <= 48


def test_protective_oco_identity_is_stable_when_an_entry_fill_grows() -> None:
    # Given: the same entry grows from a 20-share to a 35-share verified position.
    first = plan_protective_oco_exit(
        _intent(),
        _partial_state("20"),
        PaperPositionSnapshot("MSFT", Decimal(20), Decimal("2001")),
    )
    grown = plan_protective_oco_exit(
        _intent(),
        _partial_state("35"),
        PaperPositionSnapshot("MSFT", Decimal(35), Decimal("3502")),
    )

    # When / Then: quantity changes through replace semantics, not a second OCO identity.
    assert isinstance(first, ProtectiveOcoExitPlan)
    assert isinstance(grown, ProtectiveOcoExitPlan)
    assert first.client_order_id == grown.client_order_id
    assert grown.quantity == 35


def test_incomplete_execution_detail_blocks_a_protective_order_guess() -> None:
    # Given: REST reports a partial fill but execution-level evidence is incomplete.
    state = replace(_partial_state(), execution_detail_complete=False)

    # When: the protective exit planner evaluates the broker position.
    decision = plan_protective_oco_exit(
        _intent(),
        state,
        PaperPositionSnapshot("MSFT", Decimal(20), Decimal("2001")),
    )

    # Then: it refuses to infer a protective quantity from aggregate evidence alone.
    assert isinstance(decision, BlockedProtectiveExitPlan)
    assert "execution 상세" in " ".join(decision.reasons)
