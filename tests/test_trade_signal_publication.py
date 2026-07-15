from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading_agent.models import Recommendation, RecommendationState
from trading_agent.research_identity_models import (
    AgentFamily,
    MarketId,
    StrategyLaneRef,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SignalActionability,
    SignalEntryType,
    SignalSide,
    SourceCoverage,
    TradeSignalEnvelope,
    TradeTarget,
)
from trading_agent.trade_signal_publication import (
    InvalidTradeSignalPublicationInputError,
    TradeSignalPublication,
    project_trade_signal_publications,
)

OBSERVED_AT = dt.datetime(2026, 7, 15, 14, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("strategy_id", "stored_name"),
    (
        ("orb", "opening_range_breakout"),
        ("vwap_reclaim", "first_pullback_vwap_reclaim"),
        ("hod_breakout", "first_hod_volume_breakout"),
        ("gap_and_go", "five_minute_gap_hold"),
    ),
)
def test_all_existing_day_strategies_publish_conditional_signals(
    strategy_id: str,
    stored_name: str,
) -> None:
    created_at = OBSERVED_AT + dt.timedelta(seconds=10)
    published_at = created_at + dt.timedelta(seconds=5)
    recommendation = _recommendation(
        "rec-1",
        "ACME",
        stored_name,
        created_at,
    )

    publications = project_trade_signal_publications(
        (recommendation,),
        strategy_lane=_day_lane(strategy_id),
        strategy_version=f"{strategy_id}-v1",
        opportunity=_opportunity(valid_for=dt.timedelta(minutes=1)),
        published_at=published_at,
        created_after=OBSERVED_AT,
    )

    assert len(publications) == 1
    publication = publications[0]
    assert publication.published_at == published_at
    assert publication.signal.signal_id == "rec-1"
    assert publication.signal.opportunity_id == _opportunity().opportunity_id
    assert publication.signal.actionability is SignalActionability.CONDITIONAL
    assert publication.signal.quote_validation is None
    assert publication.signal.valid_until == OBSERVED_AT + dt.timedelta(minutes=1)
    assert tuple(ref.namespace for ref in publication.signal.evidence_refs) == (
        "opportunity/snapshot",
        "paper/recommendation",
    )


def test_publication_selects_only_fresh_setup_rows_from_the_exact_opportunity() -> None:
    created_at = OBSERVED_AT + dt.timedelta(seconds=10)
    recommendations = (
        _recommendation("valid", "ACME", "opening_range_breakout", created_at),
        _recommendation(
            "before-cutoff",
            "ACME",
            "opening_range_breakout",
            OBSERVED_AT - dt.timedelta(seconds=1),
        ),
        _recommendation(
            "wrong-state",
            "ACME",
            "opening_range_breakout",
            created_at,
            state=RecommendationState.ACTIVE,
        ),
        _recommendation("wrong-symbol", "OTHER", "opening_range_breakout", created_at),
        _recommendation("wrong-strategy", "ACME", "five_minute_gap_hold", created_at),
        _recommendation(
            "future",
            "ACME",
            "opening_range_breakout",
            created_at + dt.timedelta(minutes=1),
        ),
    )

    publications = project_trade_signal_publications(
        recommendations,
        strategy_lane=_day_lane("orb"),
        strategy_version="orb-v1",
        opportunity=_opportunity(valid_for=dt.timedelta(minutes=2)),
        published_at=created_at + dt.timedelta(seconds=5),
        created_after=OBSERVED_AT,
    )

    assert tuple(item.signal.signal_id for item in publications) == ("valid",)


def test_recommendations_before_or_after_the_opportunity_window_are_not_linked() -> None:
    opportunity = _opportunity(valid_for=dt.timedelta(seconds=30))
    recommendations = (
        _recommendation(
            "before-opportunity",
            "ACME",
            "opening_range_breakout",
            OBSERVED_AT - dt.timedelta(seconds=1),
        ),
        _recommendation(
            "after-opportunity",
            "ACME",
            "opening_range_breakout",
            opportunity.valid_until,
        ),
    )

    assert (
        project_trade_signal_publications(
            recommendations,
            strategy_lane=_day_lane("orb"),
            strategy_version="orb-v1",
            opportunity=opportunity,
            published_at=OBSERVED_AT + dt.timedelta(seconds=20),
            created_after=OBSERVED_AT - dt.timedelta(minutes=1),
        )
        == ()
    )


def test_stale_recommendation_is_not_published() -> None:
    created_at = OBSERVED_AT + dt.timedelta(minutes=1)
    opportunity = _opportunity(valid_for=dt.timedelta(minutes=10))

    assert (
        project_trade_signal_publications(
            (_recommendation("stale", "ACME", "opening_range_breakout", created_at),),
            strategy_lane=_day_lane("orb"),
            strategy_version="orb-v1",
            opportunity=opportunity,
            published_at=created_at + dt.timedelta(minutes=5, microseconds=1),
            created_after=OBSERVED_AT,
        )
        == ()
    )


@pytest.mark.parametrize(
    "published_at",
    (
        OBSERVED_AT.replace(tzinfo=None),
        OBSERVED_AT - dt.timedelta(seconds=1),
        OBSERVED_AT + dt.timedelta(minutes=10),
    ),
)
def test_publication_contract_rejects_invalid_publication_time(
    published_at: dt.datetime,
) -> None:
    with pytest.raises(ValidationError):
        TradeSignalPublication(
            published_at=published_at,
            signal=_signal(valid_for=dt.timedelta(minutes=10)),
        )


def test_projection_rejects_invalid_control_timestamps_and_lane() -> None:
    recommendation = _recommendation(
        "rec-1",
        "ACME",
        "opening_range_breakout",
        OBSERVED_AT + dt.timedelta(seconds=1),
    )
    with pytest.raises(InvalidTradeSignalPublicationInputError):
        project_trade_signal_publications(
            (recommendation,),
            strategy_lane=_day_lane("orb"),
            strategy_version="orb-v1",
            opportunity=_opportunity(),
            published_at=OBSERVED_AT + dt.timedelta(seconds=2),
            created_after=OBSERVED_AT.replace(tzinfo=None),
        )
    with pytest.raises(InvalidTradeSignalPublicationInputError):
        project_trade_signal_publications(
            (recommendation,),
            strategy_lane=StrategyLaneRef(
                market_id=MarketId.US_EQUITIES,
                agent_family=AgentFamily.SWING_TRADING,
                strategy_id="orb",
            ),
            strategy_version="orb-v1",
            opportunity=_opportunity(),
            published_at=OBSERVED_AT + dt.timedelta(seconds=2),
            created_after=OBSERVED_AT,
        )


def _opportunity(*, valid_for: dt.timedelta = dt.timedelta(minutes=2)) -> OpportunitySnapshot:
    return OpportunitySnapshot(
        opportunity_id="us-opportunity-20260715T140000000000Z-abcd1234",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="ranking_momentum",
        ),
        producer_strategy_version="kis-risk-screen-v1",
        observed_at=OBSERVED_AT,
        valid_until=OBSERVED_AT + valid_for,
        candidates=(
            OpportunityCandidate(
                symbol="ACME",
                rank=1,
                score=Decimal("0.12"),
                features=(FeatureValue(name="change_pct", value="0.12"),),
            ),
        ),
        evidence_refs=(
            EvidenceRef(
                namespace="kis/ranking",
                record_id="updown:NAS:1:ACME",
                observed_at=OBSERVED_AT,
            ),
        ),
        source_coverage=(
            SourceCoverage(
                source_id="kis_updown_nas",
                observed_at=OBSERVED_AT,
                record_count=1,
                complete=True,
            ),
        ),
    )


def _recommendation(
    recommendation_id: str,
    symbol: str,
    strategy: str,
    created_at: dt.datetime,
    *,
    state: RecommendationState = RecommendationState.SETUP,
) -> Recommendation:
    return Recommendation(
        recommendation_id=recommendation_id,
        symbol=symbol,
        strategy=strategy,
        created_at=created_at,
        entry=10.5,
        stop=10.0,
        target_1r=11.0,
        target_2r=11.5,
        state=state,
        rationale="ORB와 거래량 확대",
    )


def _day_lane(strategy_id: str) -> StrategyLaneRef:
    return StrategyLaneRef(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        strategy_id=strategy_id,
    )


def _signal(*, valid_for: dt.timedelta) -> TradeSignalEnvelope:
    return TradeSignalEnvelope(
        signal_id="signal-1",
        strategy_lane=_day_lane("orb"),
        producer_strategy_version="orb-v1",
        symbol="ACME",
        observed_at=OBSERVED_AT,
        valid_until=OBSERVED_AT + valid_for,
        side=SignalSide.LONG,
        entry_type=SignalEntryType.STOP_TRIGGER,
        entry_price=Decimal("10.5"),
        stop_price=Decimal("10"),
        targets=(
            TradeTarget(label="1r", price=Decimal("11")),
            TradeTarget(label="2r", price=Decimal("11.5")),
        ),
        actionability=SignalActionability.CONDITIONAL,
        invalidation_rule="진입 전 10 이하이면 무효",
        rationale="ORB와 거래량 확대",
        evidence_refs=(
            EvidenceRef(
                namespace="paper/recommendation",
                record_id="rec-1",
                observed_at=OBSERVED_AT,
            ),
        ),
    )
