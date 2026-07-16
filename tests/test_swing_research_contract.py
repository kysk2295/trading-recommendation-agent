from __future__ import annotations

import datetime as dt
from dataclasses import FrozenInstanceError

import pytest

from trading_agent.lane_contract_models import ExperimentScope, ExperimentScopeKind
from trading_agent.lane_policy_models import LaneId
from trading_agent.swing_research_contract import SWING_RESEARCH_CONTRACT


def test_swing_contract_matches_the_source_bound_hypothesis_card() -> None:
    contract = SWING_RESEARCH_CONTRACT

    assert contract.hypothesis_id == "H-SWING-NEW-HIGH-RVOL-001"
    assert contract.hypothesis == (
        "Eligible US equities that close at a 20-session high with relative volume at or above 1.5 may show "
        "conditional multi-session continuation after the registered cost model."
    )
    assert contract.falsification_rule == (
        "Reject the version when preregistered forward shadow evidence fails its fixed cost-adjusted comparison "
        "and coverage requirements."
    )
    assert contract.strategy_id == "new_high_momentum"
    assert contract.strategy_version == "new_high_rvol_20d_1p5_v1"
    assert contract.experiment_scope == ExperimentScope(
        schema_version=1,
        scope_kind=ExperimentScopeKind.SINGLE_LANE,
        hypothesis_id="H-SWING-NEW-HIGH-RVOL-001",
        primary_lane=LaneId.SWING_MOMENTUM,
        lanes=(LaneId.SWING_MOMENTUM,),
        registered_at=dt.datetime(2026, 7, 16, 20, 15, tzinfo=dt.UTC),
    )


def test_swing_contract_is_immutable_and_explicit_about_shadow_only_research() -> None:
    contract = SWING_RESEARCH_CONTRACT

    assert all(
        values
        for values in (
            contract.parameter_set,
            contract.data_contract,
            contract.cost_model,
            contract.portfolio_policy,
        )
    )
    assert "execution_costs=not_modeled" in contract.cost_model
    assert "mode=shadow_only" in contract.portfolio_policy
    assert "broker_orders=false" in contract.portfolio_policy

    with pytest.raises(FrozenInstanceError):
        contract.strategy_version = "changed"  # type: ignore[misc]
