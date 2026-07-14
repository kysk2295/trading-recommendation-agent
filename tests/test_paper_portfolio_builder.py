from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from trading_agent.execution_schema import StoredIntent
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
from trading_agent.paper_order_gate_models import (
    CompletePaperPortfolio,
    IncompletePaperPortfolio,
    PaperExposureKind,
)
from trading_agent.paper_portfolio_builder import build_paper_portfolio

INTENT_ID = IntentId("intent-1")


def _account() -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        observed_at=dt.datetime(2026, 7, 14, 13, 36, tzinfo=dt.UTC),
        status="ACTIVE",
        trading_blocked=False,
        equity=Decimal("30000"),
        last_equity=Decimal("30000"),
        buying_power=Decimal("60000"),
        account_fingerprint=AccountFingerprint("a" * 64),
    )


def _intent(*, intent_id: IntentId = INTENT_ID) -> StoredIntent:
    return StoredIntent(
        intent_id=intent_id,
        strategy_id="orb",
        strategy_version="1",
        symbol="AAPL",
        created_at="2026-07-14T09:36:00-04:00",
        side=PaperOrderSide.BUY,
        entry_limit=Decimal("100"),
        stop=Decimal("99"),
        target_1r=Decimal("101"),
        target_2r=Decimal("102"),
        quantity=50,
    )


def _order(*, filled: Decimal = Decimal(0)) -> PaperOrderSnapshot:
    return PaperOrderSnapshot(
        broker_order_id=BrokerOrderId("order-1"),
        client_order_id=INTENT_ID,
        symbol="AAPL",
        side=PaperOrderSide.BUY,
        status="partially_filled" if filled else "accepted",
        quantity=Decimal(50),
        filled_quantity=filled,
        limit_price=Decimal("100"),
        time_in_force="day",
        extended_hours=False,
    )


def _build(
    *,
    orders: tuple[PaperOrderSnapshot, ...] = (),
    positions: tuple[PaperPositionSnapshot, ...] = (),
    intents: tuple[StoredIntent, ...] = (),
    filled_intent_ids: frozenset[IntentId] = frozenset(),
) -> CompletePaperPortfolio | IncompletePaperPortfolio:
    return build_paper_portfolio(
        PaperBrokerState(_account(), orders, positions),
        intents,
        filled_intent_ids,
    )


def test_builder_derives_an_empty_portfolio_from_empty_broker_state() -> None:
    portfolio = _build()

    assert isinstance(portfolio, CompletePaperPortfolio)
    assert portfolio.exposures == ()
    assert portfolio.gross_exposure == 0
    assert portfolio.planned_open_risk == 0


def test_builder_derives_pending_order_exposure_from_broker_and_intent() -> None:
    portfolio = _build(orders=(_order(),), intents=(_intent(),))

    assert isinstance(portfolio, CompletePaperPortfolio)
    assert len(portfolio.exposures) == 1
    exposure = portfolio.exposures[0]
    assert exposure.kind is PaperExposureKind.PENDING_ENTRY
    assert exposure.gross_exposure == Decimal("5000")
    assert exposure.planned_risk == Decimal("75")


def test_builder_reserves_actual_stop_and_minimum_cost_risk_when_larger() -> None:
    larger_order = replace(
        _order(),
        quantity=Decimal(70),
    )
    larger_intent = replace(_intent(), quantity=70)

    portfolio = _build(orders=(larger_order,), intents=(larger_intent,))

    assert isinstance(portfolio, CompletePaperPortfolio)
    assert portfolio.exposures[0].planned_risk == Decimal("97.860")


def test_builder_counts_a_partial_fill_as_one_combined_exposure() -> None:
    position = PaperPositionSnapshot("AAPL", Decimal(20), Decimal("2020"))

    portfolio = _build(
        orders=(_order(filled=Decimal(20)),),
        positions=(position,),
        intents=(_intent(),),
    )

    assert isinstance(portfolio, CompletePaperPortfolio)
    assert len(portfolio.exposures) == 1
    exposure = portfolio.exposures[0]
    assert exposure.kind is PaperExposureKind.PARTIAL_ENTRY
    assert exposure.gross_exposure == Decimal("5020")
    assert portfolio.exposed_symbols == frozenset({"AAPL"})


def test_builder_floors_partial_fill_gross_at_the_intent_entry_value() -> None:
    position = PaperPositionSnapshot("AAPL", Decimal(20), Decimal("1"))

    portfolio = _build(
        orders=(_order(filled=Decimal(20)),),
        positions=(position,),
        intents=(_intent(),),
    )

    assert isinstance(portfolio, CompletePaperPortfolio)
    assert portfolio.exposures[0].gross_exposure == Decimal("5000")


def test_builder_fails_closed_on_a_torn_partial_fill_read() -> None:
    portfolio = _build(
        orders=(_order(filled=Decimal(20)),),
        intents=(_intent(),),
    )

    assert isinstance(portfolio, IncompletePaperPortfolio)
    assert any("부분체결" in reason for reason in portfolio.reasons)


def test_builder_matches_a_filled_position_to_one_current_session_intent() -> None:
    position = PaperPositionSnapshot("AAPL", Decimal(50), Decimal("5100"))

    portfolio = _build(
        positions=(position,),
        intents=(_intent(),),
        filled_intent_ids=frozenset({INTENT_ID}),
    )

    assert isinstance(portfolio, CompletePaperPortfolio)
    assert portfolio.exposures[0].kind is PaperExposureKind.OPEN_POSITION
    assert portfolio.exposures[0].gross_exposure == Decimal("5100")


def test_builder_floors_full_position_gross_at_the_intent_entry_value() -> None:
    position = PaperPositionSnapshot("AAPL", Decimal(50), Decimal("1"))

    portfolio = _build(
        positions=(position,),
        intents=(_intent(),),
        filled_intent_ids=frozenset({INTENT_ID}),
    )

    assert isinstance(portfolio, CompletePaperPortfolio)
    assert portfolio.exposures[0].gross_exposure == Decimal("5000")


@pytest.mark.parametrize("market_value", (Decimal(0), Decimal("-5000")))
def test_builder_rejects_zero_or_wrong_sign_position_value(
    market_value: Decimal,
) -> None:
    position = PaperPositionSnapshot("AAPL", Decimal(50), market_value)

    portfolio = _build(
        positions=(position,),
        intents=(_intent(),),
        filled_intent_ids=frozenset({INTENT_ID}),
    )

    assert isinstance(portfolio, IncompletePaperPortfolio)


def test_builder_fails_closed_on_ambiguous_or_unknown_positions() -> None:
    position = PaperPositionSnapshot("AAPL", Decimal(50), Decimal("5100"))
    ambiguous = _build(
        positions=(position,),
        intents=(
            _intent(),
            _intent(intent_id=IntentId("intent-2")),
        ),
        filled_intent_ids=frozenset({INTENT_ID, IntentId("intent-2")}),
    )
    unknown = _build(positions=(position,))

    assert isinstance(ambiguous, IncompletePaperPortfolio)
    assert isinstance(unknown, IncompletePaperPortfolio)


def test_builder_rejects_a_position_without_a_local_fill_event() -> None:
    position = PaperPositionSnapshot("AAPL", Decimal(50), Decimal("5100"))

    portfolio = _build(positions=(position,), intents=(_intent(),))

    assert isinstance(portfolio, IncompletePaperPortfolio)


def test_builder_fails_closed_when_partial_position_quantity_disagrees() -> None:
    position = PaperPositionSnapshot("AAPL", Decimal(19), Decimal("1919"))

    portfolio = _build(
        orders=(_order(filled=Decimal(20)),),
        positions=(position,),
        intents=(_intent(),),
    )

    assert isinstance(portfolio, IncompletePaperPortfolio)


def test_builder_uses_the_lower_equity_for_conservative_reserved_risk() -> None:
    account = replace(
        _account(),
        equity=Decimal("10000"),
        last_equity=Decimal("12000"),
    )

    portfolio = build_paper_portfolio(
        PaperBrokerState(
            account,
            (replace(_order(), quantity=Decimal(10)),),
            (),
        ),
        (replace(_intent(), quantity=10),),
        frozenset(),
    )

    assert isinstance(portfolio, CompletePaperPortfolio)
    assert portfolio.planned_open_risk == Decimal("25.0000")


def test_builder_fails_closed_on_invalid_account_money() -> None:
    invalid_account = replace(_account(), equity=Decimal("NaN"))

    portfolio = build_paper_portfolio(
        PaperBrokerState(invalid_account, (), ()),
        (),
        frozenset(),
    )

    assert isinstance(portfolio, IncompletePaperPortfolio)


def test_builder_fails_closed_on_corrupt_active_intent_geometry() -> None:
    corrupt = replace(_intent(), stop=Decimal("NaN"))

    portfolio = _build(orders=(_order(),), intents=(corrupt,))

    assert isinstance(portfolio, IncompletePaperPortfolio)
