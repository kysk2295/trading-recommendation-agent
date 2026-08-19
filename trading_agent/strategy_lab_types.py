from __future__ import annotations

import math
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.strategy_lab_errors import StrategyLabModelError


class StrategyLabId(StrEnum):
    INTRADAY_MOMENTUM = "intraday_momentum"
    INTRADAY_MEAN_REVERSION = "intraday_mean_reversion"
    CATALYST_EVENT = "catalyst_event"
    SWING_TREND_REGIME = "swing_trend_regime"
    CROSS_SECTIONAL_QUANT = "cross_sectional_quant"
    DERIVATIVES_VOLATILITY = "derivatives_volatility"


STRATEGY_LAB_IDS: Final = (
    StrategyLabId.INTRADAY_MOMENTUM,
    StrategyLabId.INTRADAY_MEAN_REVERSION,
    StrategyLabId.CATALYST_EVENT,
    StrategyLabId.SWING_TREND_REGIME,
    StrategyLabId.CROSS_SECTIONAL_QUANT,
    StrategyLabId.DERIVATIVES_VOLATILITY,
)


class EvidenceMode(StrEnum):
    HISTORICAL = "historical"
    SYNTHETIC = "synthetic"


class SignalDirection(StrEnum):
    HIGH = "high"
    LOW = "low"


class StrategyLabOutcome(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


class StrategyLabAdaptation(StrEnum):
    INITIAL = "initial"
    REPLICATION = "replication"
    BOUNDED_ALTERNATIVE = "bounded_alternative"
    MORE_EVIDENCE = "more_evidence"


class StrategyLabSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    lab_id: StrategyLabId
    feature_name: str = Field(min_length=1)
    target_name: str = Field(min_length=1)
    direction: SignalDirection
    thresholds: tuple[float, ...] = Field(min_length=1)
    economic_mechanism: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_spec(self) -> Self:
        if (
            tuple(sorted(self.thresholds)) != self.thresholds
            or len(set(self.thresholds)) != len(self.thresholds)
            or not all(math.isfinite(value) for value in self.thresholds)
        ):
            raise StrategyLabModelError("strategy lab thresholds must be ordered and unique")
        return self


_SPECS: Final = {
    StrategyLabId.INTRADAY_MOMENTUM: StrategyLabSpec(
        lab_id=StrategyLabId.INTRADAY_MOMENTUM,
        feature_name="opening_range_breakout_strength",
        target_name="same_session_net_excess_return",
        direction=SignalDirection.HIGH,
        thresholds=(1.5, 2.0),
        economic_mechanism="intraday_information_diffusion",
    ),
    StrategyLabId.INTRADAY_MEAN_REVERSION: StrategyLabSpec(
        lab_id=StrategyLabId.INTRADAY_MEAN_REVERSION,
        feature_name="volume_weighted_intraday_dislocation",
        target_name="next_hour_net_excess_return",
        direction=SignalDirection.LOW,
        thresholds=(-2.0, -1.5),
        economic_mechanism="liquidity_replenishment_after_dislocation",
    ),
    StrategyLabId.CATALYST_EVENT: StrategyLabSpec(
        lab_id=StrategyLabId.CATALYST_EVENT,
        feature_name="verified_catalyst_surprise_score",
        target_name="post_event_two_session_net_excess_return",
        direction=SignalDirection.HIGH,
        thresholds=(0.75, 1.25),
        economic_mechanism="slow_incorporation_of_verified_catalysts",
    ),
    StrategyLabId.SWING_TREND_REGIME: StrategyLabSpec(
        lab_id=StrategyLabId.SWING_TREND_REGIME,
        feature_name="trend_regime_persistence_score",
        target_name="five_session_net_excess_return",
        direction=SignalDirection.HIGH,
        thresholds=(0.6, 0.9),
        economic_mechanism="persistent_cross_session_positioning",
    ),
    StrategyLabId.CROSS_SECTIONAL_QUANT: StrategyLabSpec(
        lab_id=StrategyLabId.CROSS_SECTIONAL_QUANT,
        feature_name="cross_sectional_quality_momentum_rank",
        target_name="weekly_rank_spread_net_excess_return",
        direction=SignalDirection.HIGH,
        thresholds=(0.8, 1.2),
        economic_mechanism="slow_repricing_of_quality_momentum_dispersion",
    ),
    StrategyLabId.DERIVATIVES_VOLATILITY: StrategyLabSpec(
        lab_id=StrategyLabId.DERIVATIVES_VOLATILITY,
        feature_name="implied_realized_volatility_spread",
        target_name="delta_hedged_volatility_net_excess_return",
        direction=SignalDirection.HIGH,
        thresholds=(0.4, 0.7),
        economic_mechanism="volatility_risk_premium_normalization",
    ),
}


def strategy_lab_spec(lab_id: StrategyLabId) -> StrategyLabSpec:
    return _SPECS[lab_id]
