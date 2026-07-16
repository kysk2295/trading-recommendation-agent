from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

from trading_agent.lane_contract_models import ExperimentScope, ExperimentScopeKind
from trading_agent.lane_policy_models import LaneId


@dataclass(frozen=True, slots=True)
class SwingResearchContract:
    hypothesis_id: str
    hypothesis: str
    falsification_rule: str
    strategy_id: str
    strategy_version: str
    experiment_scope: ExperimentScope
    parameter_set: tuple[str, ...]
    data_contract: tuple[str, ...]
    cost_model: tuple[str, ...]
    portfolio_policy: tuple[str, ...]


SWING_RESEARCH_CONTRACT: Final = SwingResearchContract(
    hypothesis_id="H-SWING-NEW-HIGH-RVOL-001",
    hypothesis=(
        "Eligible US equities that close at a 20-session high with relative volume at or above 1.5 may show "
        "conditional multi-session continuation after the registered cost model."
    ),
    falsification_rule=(
        "Reject the version when preregistered forward shadow evidence fails its fixed cost-adjusted comparison "
        "and coverage requirements."
    ),
    strategy_id="new_high_momentum",
    strategy_version="new_high_rvol_20d_1p5_v1",
    experiment_scope=ExperimentScope(
        schema_version=1,
        scope_kind=ExperimentScopeKind.SINGLE_LANE,
        hypothesis_id="H-SWING-NEW-HIGH-RVOL-001",
        primary_lane=LaneId.SWING_MOMENTUM,
        lanes=(LaneId.SWING_MOMENTUM,),
        registered_at=dt.datetime(2026, 7, 16, 20, 15, tzinfo=dt.UTC),
    ),
    parameter_set=(
        "entry_buffer_bps=50",
        "lookback_sessions=20",
        "max_holding_sessions=10",
        "minimum_rvol=1.5",
        "stop_loss_bps=800",
        "target_r_multiple=2",
    ),
    data_contract=(
        "completed_daily_ohlcv_only=true",
        "point_in_time_source=true",
        "source=swing_shadow_daily_source",
    ),
    cost_model=("execution_costs=not_modeled",),
    portfolio_policy=(
        "broker_orders=false",
        "mode=shadow_only",
        "order_submission=false",
    ),
)


__all__ = ("SWING_RESEARCH_CONTRACT", "SwingResearchContract")
