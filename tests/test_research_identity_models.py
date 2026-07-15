from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent.lane_policy_models import LaneId
from trading_agent.research_identity_models import (
    AgentFamily,
    AgentManifest,
    AgentOperatingMode,
    AgentOutputKind,
    LegacyExecutionLaneBinding,
    MarketId,
    StrategyLaneRef,
)

REGISTERED_AT = dt.datetime(2026, 7, 15, 1, tzinfo=dt.UTC)


def test_strategy_lane_has_a_stable_market_agent_coordinate() -> None:
    lane = _lane(MarketId.US_EQUITIES, AgentFamily.DAY_TRADING, "orb")

    assert lane.canonical_id == "us_equities/day_trading/orb"
    assert lane.model_dump(mode="json") == {
        "schema_version": 1,
        "market_id": "us_equities",
        "agent_family": "day_trading",
        "strategy_id": "orb",
    }


def test_manifest_accepts_canonical_same_agent_lanes() -> None:
    gap = _lane(MarketId.US_EQUITIES, AgentFamily.DAY_TRADING, "gap_and_go")
    orb = _lane(MarketId.US_EQUITIES, AgentFamily.DAY_TRADING, "orb")

    manifest = AgentManifest(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        manifest_version="1.0.0",
        registered_at=REGISTERED_AT,
        output_kind=AgentOutputKind.TRADE_SIGNAL,
        operating_mode=AgentOperatingMode.ALPACA_PAPER,
        strategy_lanes=(gap, orb),
    )

    assert manifest.strategy_lanes == (gap, orb)


def test_manifest_rejects_mixed_or_noncanonical_lanes() -> None:
    us_orb = _lane(MarketId.US_EQUITIES, AgentFamily.DAY_TRADING, "orb")
    us_gap = _lane(MarketId.US_EQUITIES, AgentFamily.DAY_TRADING, "gap_and_go")
    kr_theme = _lane(MarketId.KR_EQUITIES, AgentFamily.OPPORTUNITY_MANAGER, "theme_momentum")

    with pytest.raises(ValidationError):
        _manifest(strategy_lanes=(kr_theme, us_orb))
    with pytest.raises(ValidationError):
        _manifest(strategy_lanes=(us_orb, us_gap))
    with pytest.raises(ValidationError):
        _manifest(strategy_lanes=(us_orb, us_orb))


def test_manifest_rejects_wrong_output_or_unapproved_paper_mode() -> None:
    us_orb = _lane(MarketId.US_EQUITIES, AgentFamily.DAY_TRADING, "orb")
    kr_theme = _lane(MarketId.KR_EQUITIES, AgentFamily.OPPORTUNITY_MANAGER, "theme_momentum")

    with pytest.raises(ValidationError):
        _manifest(
            output_kind=AgentOutputKind.OPPORTUNITY,
            strategy_lanes=(us_orb,),
        )
    with pytest.raises(ValidationError):
        AgentManifest(
            market_id=MarketId.KR_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            manifest_version="1.0.0",
            registered_at=REGISTERED_AT,
            output_kind=AgentOutputKind.OPPORTUNITY,
            operating_mode=AgentOperatingMode.ALPACA_PAPER,
            strategy_lanes=(kr_theme,),
        )


@pytest.mark.parametrize(
    ("family", "strategy_id", "legacy_lane_id"),
    (
        (AgentFamily.DAY_TRADING, "orb", LaneId.INTRADAY_MOMENTUM),
        (AgentFamily.SWING_TRADING, "new_high_momentum", LaneId.SWING_MOMENTUM),
        (AgentFamily.MARKET_CONTEXT, "vix", LaneId.MARKET_REGIME),
    ),
)
def test_legacy_binding_is_an_explicit_us_execution_adapter(
    family: AgentFamily,
    strategy_id: str,
    legacy_lane_id: LaneId,
) -> None:
    binding = LegacyExecutionLaneBinding(
        strategy_lane=_lane(MarketId.US_EQUITIES, family, strategy_id),
        legacy_lane_id=legacy_lane_id,
    )

    assert binding.legacy_lane_id is legacy_lane_id


@pytest.mark.parametrize(
    ("market_id", "family", "legacy_lane_id"),
    (
        (MarketId.KR_EQUITIES, AgentFamily.DAY_TRADING, LaneId.INTRADAY_MOMENTUM),
        (MarketId.KR_EQUITIES, AgentFamily.OPPORTUNITY_MANAGER, LaneId.INTRADAY_MOMENTUM),
        (MarketId.US_EQUITIES, AgentFamily.OPPORTUNITY_MANAGER, LaneId.INTRADAY_MOMENTUM),
        (MarketId.US_EQUITIES, AgentFamily.DAY_TRADING, LaneId.SWING_MOMENTUM),
    ),
)
def test_legacy_binding_rejects_unapproved_market_agent_combinations(
    market_id: MarketId,
    family: AgentFamily,
    legacy_lane_id: LaneId,
) -> None:
    with pytest.raises(ValidationError):
        LegacyExecutionLaneBinding(
            strategy_lane=_lane(market_id, family, "candidate"),
            legacy_lane_id=legacy_lane_id,
        )


def _manifest(
    *,
    output_kind: AgentOutputKind = AgentOutputKind.TRADE_SIGNAL,
    strategy_lanes: tuple[StrategyLaneRef, ...],
) -> AgentManifest:
    return AgentManifest(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        manifest_version="1.0.0",
        registered_at=REGISTERED_AT,
        output_kind=output_kind,
        operating_mode=AgentOperatingMode.CONTRACT_ONLY,
        strategy_lanes=strategy_lanes,
    )


def _lane(
    market_id: MarketId,
    family: AgentFamily,
    strategy_id: str,
) -> StrategyLaneRef:
    return StrategyLaneRef(
        market_id=market_id,
        agent_family=family,
        strategy_id=strategy_id,
    )
