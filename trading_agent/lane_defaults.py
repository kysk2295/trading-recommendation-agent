from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Final

from trading_agent.lane_contract_models import (
    ExperimentScope,
    LaneAccountBindingMode,
    LaneManifest,
    single_lane_experiment_scope,
)
from trading_agent.lane_policy_models import (
    IntradayExecutionPolicy,
    LaneId,
    LaneRiskContract,
    LaneRiskEnforcement,
    RegimeSignalExecutionPolicy,
    SwingExecutionPolicy,
)
from trading_agent.paper_risk import PaperRiskConfig

INTRADAY_EXECUTION_POLICY: Final = IntradayExecutionPolicy()
SWING_EXECUTION_POLICY: Final = SwingExecutionPolicy()
MARKET_REGIME_EXECUTION_POLICY: Final = RegimeSignalExecutionPolicy()

INTRADAY_PILOT_RISK_CONTRACT: Final = LaneRiskContract(
    enforcement=LaneRiskEnforcement.BROKER_PAPER,
    reference_equity=Decimal("30000"),
    risk_fraction=Decimal("0.0003333333333333333"),
    max_notional_dollars=Decimal("100"),
    max_planned_risk_dollars=Decimal("10"),
    max_open_positions=1,
    daily_loss_limit_dollars=Decimal("30"),
    per_side_cost_bps=Decimal("20"),
)

SWING_SHADOW_RISK_CONTRACT: Final = LaneRiskContract(
    enforcement=LaneRiskEnforcement.SHADOW,
    reference_equity=Decimal("30000"),
    risk_fraction=Decimal("0.0003333333333333333"),
    max_notional_dollars=Decimal("100"),
    max_planned_risk_dollars=Decimal("10"),
    max_open_positions=1,
    daily_loss_limit_dollars=Decimal("30"),
    per_side_cost_bps=Decimal("20"),
)

MARKET_REGIME_SIGNAL_RISK_CONTRACT: Final = LaneRiskContract(
    enforcement=LaneRiskEnforcement.NONE,
    reference_equity=Decimal(0),
    risk_fraction=Decimal(0),
    max_notional_dollars=Decimal(0),
    max_planned_risk_dollars=Decimal(0),
    max_open_positions=0,
    daily_loss_limit_dollars=Decimal(0),
    per_side_cost_bps=Decimal(0),
)

LANE_CONTRACT_REGISTERED_AT: Final = dt.datetime(2026, 7, 14, tzinfo=dt.UTC)
LANE_RISK_CONTRACT_REGISTERED_AT: Final = dt.datetime(2026, 7, 15, 1, 0, 5, tzinfo=dt.UTC)

INTRADAY_MANIFEST: Final = LaneManifest(
    lane_id=LaneId.INTRADAY_MOMENTUM,
    manifest_version="1.0.1",
    registered_at=LANE_RISK_CONTRACT_REGISTERED_AT,
    ledger_namespace="execution/intraday_momentum",
    strategy_ids=("gap_and_go", "hod_breakout", "orb", "vwap_reclaim"),
    account_binding_mode=LaneAccountBindingMode.DEDICATED_PAPER,
    execution_policy=INTRADAY_EXECUTION_POLICY,
    risk_contract=INTRADAY_PILOT_RISK_CONTRACT,
)

SWING_MANIFEST: Final = LaneManifest(
    lane_id=LaneId.SWING_MOMENTUM,
    manifest_version="1.0.1",
    registered_at=LANE_RISK_CONTRACT_REGISTERED_AT,
    ledger_namespace="execution/swing_momentum",
    strategy_ids=("new_high_momentum", "regend", "rvol"),
    account_binding_mode=LaneAccountBindingMode.FORBIDDEN,
    execution_policy=SWING_EXECUTION_POLICY,
    risk_contract=SWING_SHADOW_RISK_CONTRACT,
)

MARKET_REGIME_MANIFEST: Final = LaneManifest(
    lane_id=LaneId.MARKET_REGIME,
    manifest_version="1.0.0",
    registered_at=LANE_CONTRACT_REGISTERED_AT,
    ledger_namespace="research/market_regime",
    strategy_ids=("scr", "skew", "vix", "vix3m"),
    account_binding_mode=LaneAccountBindingMode.FORBIDDEN,
    execution_policy=MARKET_REGIME_EXECUTION_POLICY,
    risk_contract=MARKET_REGIME_SIGNAL_RISK_CONTRACT,
)

DEFAULT_LANE_MANIFESTS: Final[tuple[LaneManifest, ...]] = (
    INTRADAY_MANIFEST,
    SWING_MANIFEST,
    MARKET_REGIME_MANIFEST,
)

CURRENT_INTRADAY_EXPERIMENT_SCOPES: Final[tuple[ExperimentScope, ...]] = tuple(
    single_lane_experiment_scope(
        LaneId.INTRADAY_MOMENTUM,
        hypothesis_id,
        LANE_CONTRACT_REGISTERED_AT,
    )
    for hypothesis_id in (
        "H-MOM-GAP-001",
        "H-MOM-HOD-001",
        "H-MOM-ORB-001",
        "H-MOM-VWAP-001",
    )
)


def current_intraday_experiment_scope(hypothesis_id: str) -> ExperimentScope:
    return next(scope for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES if scope.hypothesis_id == hypothesis_id)


def intraday_pilot_paper_risk_config() -> PaperRiskConfig:
    risk = INTRADAY_PILOT_RISK_CONTRACT
    config = PaperRiskConfig(
        reference_equity=float(risk.reference_equity),
        max_risk_dollars=float(risk.max_planned_risk_dollars),
        risk_fraction=float(risk.risk_fraction),
        max_notional_dollars=float(risk.max_notional_dollars),
        max_open_positions=risk.max_open_positions,
        daily_loss_limit_dollars=float(risk.daily_loss_limit_dollars),
        per_side_cost_bps=float(risk.per_side_cost_bps),
    )
    config.assert_within_hard_limits()
    return config


INTRADAY_PILOT_PAPER_RISK_CONFIG: Final = intraday_pilot_paper_risk_config()
