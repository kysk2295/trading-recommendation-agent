from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent import research_identity_models as identity_models
from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES
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


def test_runtime_research_families_align_with_the_dashboard_primary_six() -> None:
    # Given: the runtime research-family identity contract.
    runtime_families = identity_models.RUNTIME_RESEARCH_AGENT_FAMILIES

    # When: its serialized identities are compared with the dashboard registry.
    serialized = tuple(family.value for family in runtime_families)

    # Then: exactly the primary six align and allocation authority stays separate.
    assert serialized == PRIMARY_AGENT_FAMILIES
    assert len(runtime_families) == 6
    assert AgentFamily.ALLOCATION_MANAGER not in runtime_families


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


def test_derivatives_research_manifest_has_a_distinct_contract_only_identity() -> None:
    # Given: a US derivatives research lane with no execution authority.
    lane = _lane(MarketId.US_EQUITIES, AgentFamily.DERIVATIVES_RESEARCH, "options_surface")

    # When: its runtime manifest crosses the identity-model boundary.
    manifest = AgentManifest(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DERIVATIVES_RESEARCH,
        manifest_version="1.0.0",
        registered_at=REGISTERED_AT,
        output_kind=AgentOutputKind.DERIVATIVES_RESEARCH,
        operating_mode=AgentOperatingMode.CONTRACT_ONLY,
        strategy_lanes=(lane,),
    )

    # Then: derivatives research remains distinct from market context and allocation authority.
    assert manifest.agent_family is AgentFamily.DERIVATIVES_RESEARCH
    assert manifest.output_kind is AgentOutputKind.DERIVATIVES_RESEARCH


@pytest.mark.parametrize(
    ("family", "output_kind"),
    (
        (AgentFamily.DERIVATIVES_RESEARCH, AgentOutputKind.DERIVATIVES_RESEARCH),
        (AgentFamily.ALLOCATION_MANAGER, AgentOutputKind.ALLOCATION),
    ),
)
def test_non_execution_identities_reject_paper_mode_and_legacy_binding(
    family: AgentFamily,
    output_kind: AgentOutputKind,
) -> None:
    # Given: a research-only or authority-only US identity.
    lane = _lane(MarketId.US_EQUITIES, family, "candidate")

    # When/Then: neither paper mode nor a legacy strategy execution binding is accepted.
    with pytest.raises(ValidationError):
        AgentManifest(
            market_id=MarketId.US_EQUITIES,
            agent_family=family,
            manifest_version="1.0.0",
            registered_at=REGISTERED_AT,
            output_kind=output_kind,
            operating_mode=AgentOperatingMode.ALPACA_PAPER,
            strategy_lanes=(lane,),
        )
    with pytest.raises(ValidationError):
        LegacyExecutionLaneBinding(
            strategy_lane=lane,
            legacy_lane_id=LaneId.INTRADAY_MOMENTUM,
        )


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
