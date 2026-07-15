from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from trading_agent.models import Recommendation, RecommendationState
from trading_agent.recommendation_signal_projection import (
    InvalidRecommendationSignalProjectionError,
    project_intraday_recommendation,
)
from trading_agent.research_identity_models import (
    AgentFamily,
    MarketId,
    StrategyLaneRef,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    SignalActionability,
    TradeSignalEnvelope,
)

OBSERVED_AT = dt.datetime(2026, 7, 15, 14, 31, tzinfo=dt.UTC)


def test_projects_existing_orb_setup_to_conditional_signal() -> None:
    recommendation = _recommendation()

    signal = _project(recommendation, _orb_lane())

    assert signal.signal_id == recommendation.recommendation_id
    assert signal.symbol == recommendation.symbol
    assert signal.strategy_lane.strategy_id == "orb"
    assert signal.observed_at == recommendation.created_at
    assert signal.entry_price == Decimal("10.1")
    assert signal.stop_price == Decimal("9.9")
    assert tuple(target.price for target in signal.targets) == (Decimal("10.3"), Decimal("10.5"))
    assert signal.rationale == recommendation.rationale
    assert signal.actionability is SignalActionability.CONDITIONAL
    assert signal.quote_validation is None


@pytest.mark.parametrize(
    ("strategy_id", "legacy_strategy_name"),
    (
        ("orb", "opening_range_breakout"),
        ("vwap_reclaim", "first_pullback_vwap_reclaim"),
        ("hod_breakout", "first_hod_volume_breakout"),
        ("gap_and_go", "five_minute_gap_hold"),
    ),
)
def test_projection_maps_canonical_intraday_ids_to_existing_strategy_names(
    strategy_id: str,
    legacy_strategy_name: str,
) -> None:
    signal = _project(
        replace(_recommendation(), strategy=legacy_strategy_name),
        StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id=strategy_id,
        ),
    )

    assert signal.strategy_lane.strategy_id == strategy_id


def test_projection_preserves_optional_opportunity_lineage() -> None:
    signal = _project(
        _recommendation(),
        _orb_lane(),
        opportunity_id="US-RANKING-20260715T143100Z",
    )

    assert signal.opportunity_id == "US-RANKING-20260715T143100Z"


def test_projection_rejects_mismatched_market_family_or_strategy() -> None:
    recommendation = _recommendation()
    invalid_lanes = (
        StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="vwap_reclaim",
        ),
        StrategyLaneRef(
            market_id=MarketId.KR_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="orb",
        ),
        StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.SWING_TRADING,
            strategy_id="orb",
        ),
    )

    for lane in invalid_lanes:
        with pytest.raises(InvalidRecommendationSignalProjectionError):
            _project(recommendation, lane)


@pytest.mark.parametrize(
    "state",
    tuple(state for state in RecommendationState if state is not RecommendationState.SETUP),
)
def test_projection_rejects_every_non_setup_state(state: RecommendationState) -> None:
    with pytest.raises(InvalidRecommendationSignalProjectionError):
        _project(replace(_recommendation(), state=state), _orb_lane())


def test_projection_rejects_a_naive_source_timestamp() -> None:
    recommendation = replace(
        _recommendation(),
        created_at=OBSERVED_AT.replace(tzinfo=None),
    )

    with pytest.raises(InvalidRecommendationSignalProjectionError):
        _project(recommendation, _orb_lane())


def _recommendation() -> Recommendation:
    return Recommendation(
        recommendation_id="2026-07-15T14:31:00+00:00:ABCD:orb",
        symbol="ABCD",
        strategy="opening_range_breakout",
        created_at=OBSERVED_AT,
        entry=10.10,
        stop=9.90,
        target_1r=10.30,
        target_2r=10.50,
        state=RecommendationState.SETUP,
        rationale="Opening range breakout with confirmed relative volume.",
    )


def _orb_lane() -> StrategyLaneRef:
    return StrategyLaneRef(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        strategy_id="orb",
    )


def _project(
    recommendation: Recommendation,
    lane: StrategyLaneRef,
    *,
    opportunity_id: str | None = None,
) -> TradeSignalEnvelope:
    return project_intraday_recommendation(
        recommendation,
        strategy_lane=lane,
        strategy_version="orb-v1",
        valid_until=OBSERVED_AT + dt.timedelta(minutes=2),
        evidence_refs=(
            EvidenceRef(
                namespace="recommendations",
                record_id=recommendation.recommendation_id,
                observed_at=OBSERVED_AT,
            ),
        ),
        opportunity_id=opportunity_id,
    )
