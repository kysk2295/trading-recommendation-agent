from __future__ import annotations

from enum import StrEnum
from typing import assert_never

from trading_agent.gap_strategy import FiveMinuteGapHold, GapAndGoConfig
from trading_agent.hod_strategy import FirstHodVolumeBreakout, HodBreakoutConfig
from trading_agent.strategy import OpeningRangeBreakout, OrbConfig
from trading_agent.strategy_contract import IntradayStrategy
from trading_agent.vwap_strategy import FirstPullbackVwapReclaim, VwapReclaimConfig


class StrategyMode(StrEnum):
    ORB = "orb"
    VWAP_RECLAIM = "vwap_reclaim"
    HOD_BREAKOUT = "hod_breakout"
    GAP_AND_GO = "gap_and_go"


def build_strategy(
    mode: StrategyMode,
    range_minutes: int,
) -> IntradayStrategy:
    match mode:
        case StrategyMode.ORB:
            return OpeningRangeBreakout(OrbConfig(range_minutes=range_minutes))
        case StrategyMode.VWAP_RECLAIM:
            return FirstPullbackVwapReclaim(VwapReclaimConfig())
        case StrategyMode.HOD_BREAKOUT:
            return FirstHodVolumeBreakout(HodBreakoutConfig())
        case StrategyMode.GAP_AND_GO:
            return FiveMinuteGapHold(GapAndGoConfig())
        case unreachable:
            assert_never(unreachable)
