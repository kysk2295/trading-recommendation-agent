from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading_agent.lane_defaults import (
    INTRADAY_EXECUTION_POLICY,
    INTRADAY_PILOT_RISK_CONTRACT,
    MARKET_REGIME_EXECUTION_POLICY,
    MARKET_REGIME_SIGNAL_RISK_CONTRACT,
    SWING_EXECUTION_POLICY,
    intraday_pilot_paper_risk_config,
)
from trading_agent.lane_policy_models import (
    LaneId,
    LaneRiskContract,
    LaneRiskEnforcement,
)


def test_lane_id_is_a_closed_initial_set() -> None:
    assert tuple(LaneId) == (
        LaneId.INTRADAY_MOMENTUM,
        LaneId.SWING_MOMENTUM,
        LaneId.MARKET_REGIME,
    )
    with pytest.raises(ValueError):
        _ = LaneId("post_hoc_combined_lane")


def test_default_lanes_use_distinct_execution_state_machines() -> None:
    policies = (
        INTRADAY_EXECUTION_POLICY,
        SWING_EXECUTION_POLICY,
        MARKET_REGIME_EXECUTION_POLICY,
    )

    assert tuple(policy.state_machine for policy in policies) == (
        "intraday_flat_by_close_v1",
        "swing_shadow_multisession_v1",
        "regime_signal_publish_v1",
    )
    assert len({policy.state_machine for policy in policies}) == 3
    assert INTRADAY_EXECUTION_POLICY.entry_cutoff_before_close_minutes == 30
    assert INTRADAY_EXECUTION_POLICY.flatten_before_close_minutes == 5
    assert SWING_EXECUTION_POLICY.position_states == (
        "flat",
        "entry_pending",
        "open_multisession",
        "exit_pending",
        "closed",
    )
    assert MARKET_REGIME_EXECUTION_POLICY.order_states == ()


def test_intraday_pilot_risk_contract_does_not_expand_smoke_limits() -> None:
    risk = INTRADAY_PILOT_RISK_CONTRACT

    assert risk.max_notional_dollars == Decimal("100")
    assert risk.max_planned_risk_dollars == Decimal("10")
    assert risk.max_open_positions == 1
    assert risk.daily_loss_limit_dollars == Decimal("30")
    assert risk.per_side_cost_bps == Decimal("20")

    config = intraday_pilot_paper_risk_config()
    assert config.max_notional_dollars == 100.0
    assert config.max_risk_dollars == 10.0
    assert config.max_open_positions == 1
    assert config.daily_loss_limit_dollars == 30.0
    assert config.per_side_cost_bps == 20.0


def test_signal_only_risk_contract_has_zero_execution_capacity() -> None:
    assert MARKET_REGIME_SIGNAL_RISK_CONTRACT.enforcement is LaneRiskEnforcement.NONE
    assert MARKET_REGIME_SIGNAL_RISK_CONTRACT.max_notional_dollars == 0
    assert MARKET_REGIME_SIGNAL_RISK_CONTRACT.max_planned_risk_dollars == 0
    assert MARKET_REGIME_SIGNAL_RISK_CONTRACT.max_open_positions == 0

    with pytest.raises(ValidationError):
        _ = LaneRiskContract(
            enforcement=LaneRiskEnforcement.NONE,
            reference_equity=Decimal(0),
            risk_fraction=Decimal(0),
            max_notional_dollars=Decimal(1),
            max_planned_risk_dollars=Decimal(0),
            max_open_positions=0,
            daily_loss_limit_dollars=Decimal(0),
            per_side_cost_bps=Decimal(0),
        )


def test_broker_risk_contract_rejects_hard_limit_expansion() -> None:
    with pytest.raises(ValidationError):
        _ = LaneRiskContract.model_validate(
            {
                **INTRADAY_PILOT_RISK_CONTRACT.model_dump(),
                "max_notional_dollars": Decimal("6000.01"),
            }
        )
