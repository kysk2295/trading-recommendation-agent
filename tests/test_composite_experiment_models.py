from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent.composite_experiment_models import (
    CompositeExperimentSpec,
    StrategyVersionRef,
)
from trading_agent.research_identity_models import (
    AgentFamily,
    MarketId,
    StrategyLaneRef,
)

REGISTERED_AT = dt.datetime(2026, 7, 15, 1, tzinfo=dt.UTC)
EFFECTIVE_AT = dt.datetime(
    2026,
    7,
    16,
    9,
    tzinfo=dt.timezone(dt.timedelta(hours=9)),
)


def test_kr_theme_and_day_versions_form_a_preregistered_composite() -> None:
    day = _kr_version(AgentFamily.DAY_TRADING, "theme_vwap_pullback", "kr-theme-day-v1")
    manager = _kr_version(AgentFamily.OPPORTUNITY_MANAGER, "theme_momentum", "kr-theme-manager-v1")

    spec = _spec(day, manager)

    assert spec.primary_lane == day.strategy_lane
    assert spec.component_versions == tuple(sorted((day, manager), key=lambda item: item.canonical_id))
    assert spec.component_versions[0].canonical_id == (
        "kr_equities/day_trading/theme_vwap_pullback@kr-theme-day-v1"
    )


def test_composite_rejects_post_hoc_effective_time() -> None:
    day = _kr_version(AgentFamily.DAY_TRADING, "theme_vwap_pullback", "kr-theme-day-v1")
    manager = _kr_version(AgentFamily.OPPORTUNITY_MANAGER, "theme_momentum", "kr-theme-manager-v1")

    with pytest.raises(ValidationError):
        _spec(day, manager, effective_at=REGISTERED_AT)


def test_composite_rejects_cross_market_components() -> None:
    day = _kr_version(AgentFamily.DAY_TRADING, "theme_vwap_pullback", "kr-theme-day-v1")
    us_orb = StrategyVersionRef(
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="orb",
        ),
        strategy_version="orb-v1",
    )

    with pytest.raises(ValidationError):
        _spec(day, us_orb)


def test_composite_rejects_duplicates_or_one_lane_disguised_as_two_versions() -> None:
    day_v1 = _kr_version(AgentFamily.DAY_TRADING, "theme_vwap_pullback", "kr-theme-day-v1")
    day_v2 = _kr_version(AgentFamily.DAY_TRADING, "theme_vwap_pullback", "kr-theme-day-v2")

    with pytest.raises(ValidationError):
        _spec(day_v1, day_v1)
    with pytest.raises(ValidationError):
        _spec(day_v1, day_v2)


def test_composite_requires_canonical_component_order_and_primary_membership() -> None:
    day = _kr_version(AgentFamily.DAY_TRADING, "theme_vwap_pullback", "kr-theme-day-v1")
    manager = _kr_version(AgentFamily.OPPORTUNITY_MANAGER, "theme_momentum", "kr-theme-manager-v1")

    with pytest.raises(ValidationError):
        CompositeExperimentSpec(
            experiment_id="KR-THEME-DAY-001",
            primary_lane=day.strategy_lane,
            component_versions=(manager, day),
            combination_rule="Use the frozen theme ranking as the only candidate universe for the day rule.",
            registered_at=REGISTERED_AT,
            effective_at=EFFECTIVE_AT,
        )

    missing_primary = _kr_version(AgentFamily.DAY_TRADING, "theme_second_wave", "kr-theme-wave-v1")
    with pytest.raises(ValidationError):
        CompositeExperimentSpec(
            experiment_id="KR-THEME-DAY-002",
            primary_lane=missing_primary.strategy_lane,
            component_versions=tuple(sorted((day, manager), key=lambda item: item.canonical_id)),
            combination_rule="Use the frozen theme ranking as the only candidate universe for the day rule.",
            registered_at=REGISTERED_AT,
            effective_at=EFFECTIVE_AT,
        )


def _spec(
    first: StrategyVersionRef,
    second: StrategyVersionRef,
    *,
    effective_at: dt.datetime = EFFECTIVE_AT,
) -> CompositeExperimentSpec:
    components = tuple(sorted((first, second), key=lambda item: item.canonical_id))
    return CompositeExperimentSpec(
        experiment_id="KR-THEME-DAY-001",
        primary_lane=first.strategy_lane,
        component_versions=components,
        combination_rule="Use the frozen theme ranking as the only candidate universe for the day rule.",
        registered_at=REGISTERED_AT,
        effective_at=effective_at,
    )


def _kr_version(
    family: AgentFamily,
    strategy_id: str,
    version: str,
) -> StrategyVersionRef:
    return StrategyVersionRef(
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.KR_EQUITIES,
            agent_family=family,
            strategy_id=strategy_id,
        ),
        strategy_version=version,
    )
