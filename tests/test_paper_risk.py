from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from trading_agent.paper_execution_models import (
    IntentId,
    PaperOrderIntent,
    PaperOrderSide,
)
from trading_agent.paper_risk import (
    PaperRiskConfig,
    PaperSizingContext,
    UnsafePaperRiskConfigError,
    size_paper_order,
)

NEW_YORK = ZoneInfo("America/New_York")


def _intent(
    entry: float = 10.0,
    stop: float = 9.75,
    side: PaperOrderSide = PaperOrderSide.BUY,
) -> PaperOrderIntent:
    risk = abs(entry - stop)
    target_direction = 1.0 if side is PaperOrderSide.BUY else -1.0
    return PaperOrderIntent(
        intent_id=IntentId("orb-v1-20260714-AAA-093600"),
        strategy_id="orb",
        strategy_version="1.0.0",
        symbol="AAA",
        created_at=dt.datetime(2026, 7, 14, 9, 36, tzinfo=NEW_YORK),
        side=side,
        entry_limit=entry,
        stop=stop,
        target_1r=entry + target_direction * risk,
        target_2r=entry + target_direction * 2.0 * risk,
    )


def test_sizing_keeps_distance_and_20bp_cost_inside_75_dollar_cap() -> None:
    # Given
    context = PaperSizingContext(
        conservative_equity=30_000.0,
        liquidity_allowed_quantity=10_000,
        estimated_spread_bps=0.0,
    )

    # When
    sized = size_paper_order(_intent(), context)

    # Then
    assert sized is not None
    assert sized.quantity == 259
    assert sized.planned_risk == pytest.approx(74.9805)
    assert sized.planned_risk <= 75.0


def test_sizing_uses_6000_dollar_notional_cap() -> None:
    # Given
    context = PaperSizingContext(30_000.0, 10_000, estimated_spread_bps=0.0)

    # When
    sized = size_paper_order(_intent(entry=100.0, stop=99.8), context)

    # Then
    assert sized is not None
    assert sized.quantity == 60
    assert sized.notional == pytest.approx(6_000.0)


def test_sizing_reduces_risk_after_drawdown() -> None:
    # Given
    context = PaperSizingContext(20_000.0, 10_000, estimated_spread_bps=0.0)

    # When
    sized = size_paper_order(_intent(), context)

    # Then
    assert sized is not None
    assert sized.quantity == 172
    assert sized.planned_risk <= 50.0


def test_sizing_includes_observed_spread_in_planned_risk() -> None:
    # Given
    narrow_spread = PaperSizingContext(30_000.0, 10_000, estimated_spread_bps=10.0)
    wide_spread = PaperSizingContext(30_000.0, 10_000, estimated_spread_bps=80.0)

    # When
    narrow = size_paper_order(_intent(), narrow_spread)
    wide = size_paper_order(_intent(), wide_spread)

    # Then
    assert narrow is not None
    assert wide is not None
    assert wide.quantity < narrow.quantity
    assert wide.planned_risk <= 75.0


def test_sizing_supports_short_distance_without_mixing_direction() -> None:
    # Given
    context = PaperSizingContext(30_000.0, 10_000, estimated_spread_bps=0.0)

    # When
    sized = size_paper_order(
        _intent(entry=10.0, stop=10.25, side=PaperOrderSide.SELL),
        context,
    )

    # Then
    assert sized is not None
    assert sized.quantity > 0
    assert sized.planned_risk <= 75.0


@pytest.mark.parametrize(
    ("intent", "context"),
    (
        (_intent(stop=10.0), PaperSizingContext(30_000.0, 10_000, 0.0)),
        (_intent(), PaperSizingContext(30_000.0, 0, 0.0)),
        (_intent(), PaperSizingContext(0.0, 10_000, 0.0)),
        (_intent(), PaperSizingContext(30_000.0, 10_000, -1.0)),
    ),
)
def test_sizing_rejects_invalid_risk_boundaries(
    intent: PaperOrderIntent,
    context: PaperSizingContext,
) -> None:
    # Given / When
    sized = size_paper_order(intent, context)

    # Then
    assert sized is None


@pytest.mark.parametrize("invalid_value", (float("nan"), float("inf"), -float("inf")))
def test_sizing_rejects_nonfinite_market_values(invalid_value: float) -> None:
    # Given
    context = PaperSizingContext(30_000.0, 10_000, invalid_value)

    # When
    sized = size_paper_order(_intent(entry=invalid_value), context)

    # Then
    assert sized is None


@pytest.mark.parametrize(
    "config",
    (
        PaperRiskConfig(max_risk_dollars=75.01),
        PaperRiskConfig(risk_fraction=0.0026),
        PaperRiskConfig(max_notional_dollars=6_001.0),
        PaperRiskConfig(max_open_positions=4),
        PaperRiskConfig(daily_loss_limit_dollars=301.0),
        PaperRiskConfig(per_side_cost_bps=19.9),
    ),
)
def test_risk_config_rejects_every_looser_hard_limit(config: PaperRiskConfig) -> None:
    # Given / When / Then
    with pytest.raises(UnsafePaperRiskConfigError, match="완화"):
        config.assert_within_hard_limits()
