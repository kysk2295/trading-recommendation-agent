from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent.experiment_scope_models import ExperimentScopeKind
from trading_agent.multi_market_experiment_models import (
    MultiMarketExperimentScope,
    MultiMarketStrategyVersionRegistration,
    multi_market_experiment_scope_key,
)
from trading_agent.research_identity_models import (
    AgentFamily,
    AgentOperatingMode,
    MarketId,
    StrategyLaneRef,
)

REGISTERED_AT = dt.datetime(2026, 7, 19, 8, tzinfo=dt.UTC)


def test_kr_shadow_scope_and_version_are_first_class() -> None:
    scope = _single_scope(_lane(MarketId.KR_EQUITIES, AgentFamily.DAY_TRADING))

    version = _version(scope, AgentOperatingMode.SHADOW)

    assert version.strategy_lane.market_id is MarketId.KR_EQUITIES
    assert version.operating_mode is AgentOperatingMode.SHADOW


def test_kr_strategy_cannot_claim_alpaca_paper_mode() -> None:
    scope = _single_scope(_lane(MarketId.KR_EQUITIES, AgentFamily.DAY_TRADING))

    with pytest.raises(ValidationError, match="invalid multi-market experiment model"):
        _ = _version(scope, AgentOperatingMode.ALPACA_PAPER)


def test_scope_rejects_cross_market_component_lanes() -> None:
    kr = _lane(MarketId.KR_EQUITIES, AgentFamily.OPPORTUNITY_MANAGER)
    us = _lane(MarketId.US_EQUITIES, AgentFamily.MARKET_CONTEXT)

    with pytest.raises(ValidationError, match="invalid multi-market experiment model"):
        _ = MultiMarketExperimentScope(
            scope_kind=ExperimentScopeKind.CROSS_LANE_HYPOTHESIS,
            hypothesis_id="H-CROSS-MARKET-001",
            primary_lane=kr,
            lanes=tuple(sorted((kr, us), key=lambda lane: lane.canonical_id)),
            source_hypothesis_ids=("H-KR-001", "H-US-001"),
            combination_rule="Pre-registered cross-market rule.",
            registered_at=REGISTERED_AT,
        )


def _single_scope(lane: StrategyLaneRef) -> MultiMarketExperimentScope:
    return MultiMarketExperimentScope(
        scope_kind=ExperimentScopeKind.SINGLE_LANE,
        hypothesis_id="H-KR-DAY-001",
        primary_lane=lane,
        lanes=(lane,),
        registered_at=REGISTERED_AT,
    )


def _version(
    scope: MultiMarketExperimentScope,
    operating_mode: AgentOperatingMode,
) -> MultiMarketStrategyVersionRegistration:
    return MultiMarketStrategyVersionRegistration(
        strategy_version="kr_day_v1",
        hypothesis_id=scope.hypothesis_id,
        experiment_scope_key=multi_market_experiment_scope_key(scope),
        strategy_lane=scope.primary_lane,
        operating_mode=operating_mode,
        code_version="kr-day-code-v1",
        parameter_set=("entry:next_bar",),
        data_contract=("kr_intraday_v1",),
        cost_model=("kr_shadow_cost_v1",),
        portfolio_policy=("shadow_only",),
        source_registered_at=REGISTERED_AT,
        ledger_recorded_at=REGISTERED_AT,
    )


def _lane(market: MarketId, family: AgentFamily) -> StrategyLaneRef:
    return StrategyLaneRef(
        market_id=market,
        agent_family=family,
        strategy_id="strategy",
    )
