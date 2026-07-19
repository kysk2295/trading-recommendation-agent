from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent.lane_identity_models import LaneId
from trading_agent.research_identity_models import (
    AgentFamily,
    AgentOperatingMode,
    MarketId,
    StrategyLaneRef,
)
from trading_agent.strategy_authority_models import StrategyAuthorityBinding

BOUND_AT = dt.datetime(2026, 7, 19, 8, tzinfo=dt.UTC)


def test_shadow_authority_accepts_approved_us_swing_binding() -> None:
    binding = StrategyAuthorityBinding(
        strategy_version="swing_new_high_rvol_v1",
        strategy_lane=_lane(MarketId.US_EQUITIES, AgentFamily.SWING_TRADING),
        operating_mode=AgentOperatingMode.SHADOW,
        legacy_lane_id=LaneId.SWING_MOMENTUM,
        bound_at=BOUND_AT,
    )

    assert binding.operating_mode is AgentOperatingMode.SHADOW


def test_authority_rejects_kr_lane_without_legacy_execution_mapping() -> None:
    with pytest.raises(ValidationError, match="execution binding"):
        _ = StrategyAuthorityBinding(
            strategy_version="kr_theme_v1",
            strategy_lane=_lane(MarketId.KR_EQUITIES, AgentFamily.DAY_TRADING),
            operating_mode=AgentOperatingMode.SHADOW,
            legacy_lane_id=LaneId.INTRADAY_MOMENTUM,
            bound_at=BOUND_AT,
        )


def test_authority_rejects_alpaca_paper_market_context() -> None:
    with pytest.raises(ValidationError, match="not Alpaca Paper eligible"):
        _ = StrategyAuthorityBinding(
            strategy_version="market_context_v1",
            strategy_lane=_lane(MarketId.US_EQUITIES, AgentFamily.MARKET_CONTEXT),
            operating_mode=AgentOperatingMode.ALPACA_PAPER,
            legacy_lane_id=LaneId.MARKET_REGIME,
            bound_at=BOUND_AT,
        )


def _lane(market_id: MarketId, family: AgentFamily) -> StrategyLaneRef:
    return StrategyLaneRef(
        market_id=market_id,
        agent_family=family,
        strategy_id="strategy",
    )
