from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent.market_context_models import (
    MarketContextBindingRule,
    MarketContextSnapshot,
    MarketRegimeLabel,
    context_is_usable,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import FeatureValue, SourceCoverage

UTC = dt.UTC
OBSERVED = dt.datetime(2026, 7, 21, 14, 30, tzinfo=UTC)


def test_market_context_snapshot_is_research_only() -> None:
    snapshot = _snapshot()
    assert snapshot.order_authority is False
    assert snapshot.allocation_authority is False
    assert snapshot.lifecycle_authority is False
    assert snapshot.regime_labels == (MarketRegimeLabel.HIGH_VOL, MarketRegimeLabel.TRENDING)


def test_binding_rule_requires_exact_producer_version_and_freshness() -> None:
    snapshot = _snapshot()
    rule = MarketContextBindingRule(
        strategy_lane_canonical_id="us_equities/day_trading/orb",
        required_context_producer_version="market-context-v1",
        max_context_age_seconds=300,
        allow_unknown_regime=False,
    )
    assert context_is_usable(snapshot, rule, as_of=OBSERVED + dt.timedelta(seconds=60)) is True
    assert context_is_usable(snapshot, rule, as_of=OBSERVED + dt.timedelta(seconds=301)) is False
    wrong = rule.model_copy(update={"required_context_producer_version": "other-v1"})
    assert context_is_usable(snapshot, wrong, as_of=OBSERVED + dt.timedelta(seconds=60)) is False


def test_unknown_regime_alone_is_allowed_but_not_mixed() -> None:
    alone = MarketContextSnapshot(
        context_id="us-context-unknown",
        market_id=MarketId.US_EQUITIES,
        observed_at=OBSERVED,
        valid_until=OBSERVED + dt.timedelta(minutes=5),
        regime_labels=(MarketRegimeLabel.UNKNOWN,),
        breadth_and_volatility_features=(
            FeatureValue(name="advance_decline", value="1.0"),
        ),
        macro_and_flow_refs=(),
        coverage=(
            SourceCoverage(
                source_id="internal_breadth",
                observed_at=OBSERVED,
                record_count=1,
                complete=True,
            ),
        ),
        producer_version="market-context-v1",
    )
    assert alone.regime_labels == (MarketRegimeLabel.UNKNOWN,)
    with pytest.raises((ValidationError, ValueError)):
        MarketContextSnapshot(
            context_id="us-context-mixed-unknown",
            market_id=MarketId.US_EQUITIES,
            observed_at=OBSERVED,
            valid_until=OBSERVED + dt.timedelta(minutes=5),
            regime_labels=(MarketRegimeLabel.RISK_ON, MarketRegimeLabel.UNKNOWN),
            breadth_and_volatility_features=(
                FeatureValue(name="advance_decline", value="1.0"),
            ),
            macro_and_flow_refs=(),
            coverage=(
                SourceCoverage(
                    source_id="internal_breadth",
                    observed_at=OBSERVED,
                    record_count=1,
                    complete=True,
                ),
            ),
            producer_version="market-context-v1",
        )


def _snapshot() -> MarketContextSnapshot:
    return MarketContextSnapshot(
        context_id="us-context-20260721t1430",
        market_id=MarketId.US_EQUITIES,
        observed_at=OBSERVED,
        valid_until=OBSERVED + dt.timedelta(minutes=5),
        regime_labels=(MarketRegimeLabel.HIGH_VOL, MarketRegimeLabel.TRENDING),
        breadth_and_volatility_features=(
            FeatureValue(name="advance_decline", value="1.25"),
            FeatureValue(name="realized_vol_20d", value="0.22"),
        ),
        macro_and_flow_refs=("fred.vix",),
        coverage=(
            SourceCoverage(
                source_id="internal_breadth",
                observed_at=OBSERVED,
                record_count=500,
                complete=True,
            ),
        ),
        producer_version="market-context-v1",
    )
