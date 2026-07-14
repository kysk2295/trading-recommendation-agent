from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.paper_order_gate_fixtures import evaluate, exposure, snapshot
from trading_agent.paper_execution_models import PaperOrderIntent, PaperOrderSide
from trading_agent.paper_order_gate_models import (
    ApprovedPaperOrderGateDecision,
    CompletePaperPortfolio,
    IncompletePaperPortfolio,
    PaperOrderGateDecision,
    PaperOrderGateState,
)


def _portfolio() -> CompletePaperPortfolio:
    portfolio = snapshot().portfolio
    assert isinstance(portfolio, CompletePaperPortfolio)
    return portfolio


def _approved(
    decision: PaperOrderGateDecision,
) -> ApprovedPaperOrderGateDecision:
    assert isinstance(decision, ApprovedPaperOrderGateDecision)
    return decision


def test_gate_derives_quantity_with_mandatory_round_trip_cost_reserve() -> None:
    decision = evaluate(snapshot())

    decision = _approved(decision)
    assert decision.sized_order.risk_per_share == pytest.approx(1.398)
    assert decision.sized_order.quantity == 53
    assert decision.sized_order.planned_risk == pytest.approx(74.094)


def test_spread_cost_is_mandatory_and_reduces_approved_quantity() -> None:
    with_spread = replace(snapshot(), estimated_spread_bps=20.0)

    decision = evaluate(with_spread)

    decision = _approved(decision)
    assert decision.sized_order.risk_per_share == pytest.approx(1.598)
    assert decision.sized_order.quantity == 46
    assert decision.sized_order.planned_risk <= 75


def test_gate_accepts_a_flat_two_slot_portfolio_boundary() -> None:
    portfolio = replace(
        _portfolio(),
        exposures=(
            exposure("MSFT", gross="6000"),
            exposure("NVDA", gross="6000"),
        ),
    )
    gate_snapshot = replace(
        snapshot(),
        portfolio=portfolio,
        liquidity_allowed_quantity=50,
    )

    decision = evaluate(gate_snapshot)

    assert decision.state is PaperOrderGateState.APPROVED


def test_gate_rechecks_equity_fraction_during_internal_sizing() -> None:
    portfolio = replace(
        _portfolio(),
        equity=Decimal("10000"),
        last_equity=Decimal("10000"),
    )

    decision = evaluate(replace(snapshot(), portfolio=portfolio))

    decision = _approved(decision)
    assert decision.sized_order.planned_risk <= 25
    assert decision.sized_order.notional <= 2_000


@pytest.mark.parametrize(
    "portfolio",
    (
        replace(
            _portfolio(),
            exposures=(exposure("AAPL"),),
        ),
        replace(
            _portfolio(),
            exposures=(
                exposure("MSFT"),
                exposure("NVDA"),
                exposure("TSLA"),
            ),
        ),
        replace(_portfolio(), buying_power=Decimal("5299.99")),
        replace(_portfolio(), trading_blocked=True),
        replace(
            _portfolio(),
            equity=Decimal("29700"),
            last_equity=Decimal("30000"),
        ),
        replace(
            _portfolio(),
            exposures=(exposure("MSFT", gross="13000"),),
        ),
        replace(
            _portfolio(),
            exposures=(exposure("MSFT", risk="75.01"),),
        ),
        replace(
            _portfolio(),
            exposures=(exposure("MSFT", gross="6000.01"),),
        ),
    ),
)
def test_gate_blocks_each_hard_portfolio_limit(
    portfolio: CompletePaperPortfolio,
) -> None:
    decision = evaluate(replace(snapshot(), portfolio=portfolio))

    assert decision.state is PaperOrderGateState.PORTFOLIO_BLOCKED


@pytest.mark.parametrize(
    "intent",
    (
        replace(snapshot().candidate_intent, stop=101.0),
        replace(
            snapshot().candidate_intent,
            side=PaperOrderSide.SELL,
            stop=99.0,
            target_1r=98.0,
            target_2r=97.0,
        ),
        replace(snapshot().candidate_intent, target_1r=99.5),
    ),
)
def test_gate_rechecks_order_geometry(intent: PaperOrderIntent) -> None:
    decision = evaluate(replace(snapshot(), candidate_intent=intent))

    assert decision.state is PaperOrderGateState.PORTFOLIO_BLOCKED


def test_gate_fails_closed_when_portfolio_aggregation_is_incomplete() -> None:
    gate_snapshot = replace(
        snapshot(),
        portfolio=IncompletePaperPortfolio(("pending order risk missing",)),
    )

    decision = evaluate(gate_snapshot)

    assert decision.state is PaperOrderGateState.PORTFOLIO_BLOCKED


def test_gate_fails_closed_on_invalid_liquidity_or_spread_inputs() -> None:
    no_liquidity = evaluate(
        replace(snapshot(), liquidity_allowed_quantity=0)
    )
    negative_spread = evaluate(replace(snapshot(), estimated_spread_bps=-1.0))

    assert no_liquidity.state is PaperOrderGateState.PORTFOLIO_BLOCKED
    assert negative_spread.state is PaperOrderGateState.PORTFOLIO_BLOCKED
