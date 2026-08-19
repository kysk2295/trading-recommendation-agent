from __future__ import annotations

import datetime as dt
from decimal import Decimal

from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)
from trading_agent.strategy_research_forward_observations import (
    project_matured_intraday_observations,
)

NOW = dt.datetime(2026, 8, 19, 14, 20, tzinfo=dt.UTC)


def test_projects_real_net_forward_return_only_after_thirty_minute_maturity() -> None:
    entry = _snapshot("entry", NOW - dt.timedelta(minutes=40), "SPY", Decimal("500"), Decimal("2"))
    exit_ = _snapshot("exit", NOW - dt.timedelta(minutes=9), "SPY", Decimal("505"), Decimal("4"))

    observations = project_matured_intraday_observations((entry, exit_), NOW)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.market_id is MarketId.US_EQUITIES
    assert observation.source_opportunity_id == "entry"
    assert observation.gross_return == Decimal("0.01")
    assert observation.net_return == Decimal("0.0097")
    assert observation.real_market_evidence is True
    assert observation.profitability_claim is False
    assert observation.trading_authority is False


def test_does_not_backfill_unmatured_or_cross_market_entries() -> None:
    fresh = _snapshot("fresh", NOW - dt.timedelta(minutes=20), "SPY", Decimal("500"), Decimal("2"))
    kr_exit = _snapshot(
        "kr-exit",
        NOW - dt.timedelta(minutes=1),
        "005930",
        Decimal("250000"),
        Decimal("5"),
        market=MarketId.KR_EQUITIES,
    )

    assert project_matured_intraday_observations((fresh, kr_exit), NOW) == ()


def _snapshot(
    identity: str,
    observed_at: dt.datetime,
    symbol: str,
    close: Decimal,
    spread_bps: Decimal,
    *,
    market: MarketId = MarketId.US_EQUITIES,
) -> OpportunitySnapshot:
    return OpportunitySnapshot(
        opportunity_id=identity,
        strategy_lane=StrategyLaneRef(
            market_id=market,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="us_intraday_momentum" if market is MarketId.US_EQUITIES else "theme_momentum",
        ),
        producer_strategy_version="test-v1",
        observed_at=observed_at,
        valid_until=observed_at + dt.timedelta(minutes=1),
        candidates=(
            OpportunityCandidate(
                symbol=symbol,
                rank=1,
                score=Decimal("1"),
                features=(
                    FeatureValue(name="completed_bar_close", value=str(close)),
                    FeatureValue(name="completed_bar_end_at", value=observed_at.isoformat()),
                    FeatureValue(name="spread_bps", value=str(spread_bps)),
                ),
            ),
        ),
        evidence_refs=(EvidenceRef(namespace="test/source", record_id=identity, observed_at=observed_at),),
        source_coverage=(
            SourceCoverage(
                source_id="test_source",
                observed_at=observed_at,
                record_count=1,
                complete=True,
            ),
        ),
    )
