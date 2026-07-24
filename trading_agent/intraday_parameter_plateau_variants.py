from __future__ import annotations

from dataclasses import dataclass, replace
from typing import assert_never

from trading_agent.gap_strategy import FiveMinuteGapHold, GapAndGoConfig
from trading_agent.hod_strategy import FirstHodVolumeBreakout, HodBreakoutConfig
from trading_agent.intraday_parameter_plateau_trace_models import (
    InvalidIntradayParameterPlateauError,
)
from trading_agent.strategy_contract import IntradayStrategy
from trading_agent.strategy_factory import StrategyMode
from trading_agent.vwap_strategy import (
    FirstPullbackVwapReclaim,
    VwapReclaimConfig,
)


@dataclass(frozen=True, slots=True)
class VwapParameterVariant:
    variant_id: str
    config: VwapReclaimConfig

    @property
    def is_center(self) -> bool:
        return self.variant_id == "center"

    @property
    def parameter_set(self) -> tuple[str, ...]:
        config = self.config
        return (
            f"min_extension_pct={config.min_extension_pct:g}",
            f"touch_tolerance_bps={config.touch_tolerance_bps:g}",
            f"reclaim_buffer_bps={config.reclaim_buffer_bps:g}",
            f"volume_multiplier={config.volume_multiplier:g}",
            f"max_reclaim_bars={config.max_reclaim_bars}",
        )


@dataclass(frozen=True, slots=True)
class HodParameterVariant:
    variant_id: str
    config: HodBreakoutConfig

    @property
    def is_center(self) -> bool:
        return self.variant_id == "center"

    @property
    def parameter_set(self) -> tuple[str, ...]:
        config = self.config
        return (
            f"min_hod_gain_pct={config.min_hod_gain_pct:g}",
            f"breakout_buffer_bps={config.breakout_buffer_bps:g}",
            f"volume_multiplier={config.volume_multiplier:g}",
            f"min_base_bars={config.min_base_bars}",
            f"max_base_bars={config.max_base_bars}",
            f"max_pullback_pct={config.max_pullback_pct:g}",
        )


@dataclass(frozen=True, slots=True)
class GapParameterVariant:
    variant_id: str
    config: GapAndGoConfig

    @property
    def is_center(self) -> bool:
        return self.variant_id == "center"

    @property
    def parameter_set(self) -> tuple[str, ...]:
        config = self.config
        return (
            f"opening_minutes={config.opening_minutes}",
            f"min_gap_pct={config.min_gap_pct:g}",
            f"min_gap_retention={config.min_gap_retention:g}",
            f"entry_buffer_bps={config.entry_buffer_bps:g}",
        )


type IntradayParameterVariant = (
    VwapParameterVariant | HodParameterVariant | GapParameterVariant
)


def parameter_variants(
    strategy: StrategyMode,
) -> tuple[IntradayParameterVariant, ...]:
    match strategy:
        case StrategyMode.VWAP_RECLAIM:
            center = VwapReclaimConfig()
            return (
                VwapParameterVariant("center", center),
                VwapParameterVariant(
                    "min_extension_pct:lower",
                    replace(center, min_extension_pct=0.0075),
                ),
                VwapParameterVariant(
                    "min_extension_pct:upper",
                    replace(center, min_extension_pct=0.0125),
                ),
                VwapParameterVariant(
                    "touch_tolerance_bps:lower",
                    replace(center, touch_tolerance_bps=15.0),
                ),
                VwapParameterVariant(
                    "touch_tolerance_bps:upper",
                    replace(center, touch_tolerance_bps=25.0),
                ),
                VwapParameterVariant(
                    "volume_multiplier:lower",
                    replace(center, volume_multiplier=1.0),
                ),
                VwapParameterVariant(
                    "volume_multiplier:upper",
                    replace(center, volume_multiplier=1.4),
                ),
            )
        case StrategyMode.HOD_BREAKOUT:
            center = HodBreakoutConfig()
            return (
                HodParameterVariant("center", center),
                HodParameterVariant(
                    "min_hod_gain_pct:lower",
                    replace(center, min_hod_gain_pct=0.02),
                ),
                HodParameterVariant(
                    "min_hod_gain_pct:upper",
                    replace(center, min_hod_gain_pct=0.04),
                ),
                HodParameterVariant(
                    "volume_multiplier:lower",
                    replace(center, volume_multiplier=1.25),
                ),
                HodParameterVariant(
                    "volume_multiplier:upper",
                    replace(center, volume_multiplier=1.75),
                ),
                HodParameterVariant(
                    "min_base_bars:lower",
                    replace(center, min_base_bars=1),
                ),
                HodParameterVariant(
                    "min_base_bars:upper",
                    replace(center, min_base_bars=3),
                ),
            )
        case StrategyMode.GAP_AND_GO:
            center = GapAndGoConfig()
            return (
                GapParameterVariant("center", center),
                GapParameterVariant(
                    "opening_minutes:lower",
                    replace(center, opening_minutes=3),
                ),
                GapParameterVariant(
                    "opening_minutes:upper",
                    replace(center, opening_minutes=7),
                ),
                GapParameterVariant(
                    "min_gap_pct:lower",
                    replace(center, min_gap_pct=0.03),
                ),
                GapParameterVariant(
                    "min_gap_pct:upper",
                    replace(center, min_gap_pct=0.05),
                ),
                GapParameterVariant(
                    "min_gap_retention:lower",
                    replace(center, min_gap_retention=0.4),
                ),
                GapParameterVariant(
                    "min_gap_retention:upper",
                    replace(center, min_gap_retention=0.6),
                ),
            )
        case StrategyMode.ORB:
            raise InvalidIntradayParameterPlateauError
        case unreachable:
            assert_never(unreachable)


def parameter_variant_strategy(
    variant: IntradayParameterVariant,
) -> StrategyMode:
    match variant:
        case VwapParameterVariant():
            return StrategyMode.VWAP_RECLAIM
        case HodParameterVariant():
            return StrategyMode.HOD_BREAKOUT
        case GapParameterVariant():
            return StrategyMode.GAP_AND_GO
        case unreachable:
            assert_never(unreachable)


def build_parameter_variant_strategy(
    variant: IntradayParameterVariant,
) -> IntradayStrategy:
    match variant:
        case VwapParameterVariant(config=config):
            return FirstPullbackVwapReclaim(config)
        case HodParameterVariant(config=config):
            return FirstHodVolumeBreakout(config)
        case GapParameterVariant(config=config):
            return FiveMinuteGapHold(config)
        case unreachable:
            assert_never(unreachable)


__all__ = (
    "GapParameterVariant",
    "HodParameterVariant",
    "IntradayParameterVariant",
    "VwapParameterVariant",
    "build_parameter_variant_strategy",
    "parameter_variant_strategy",
    "parameter_variants",
)
