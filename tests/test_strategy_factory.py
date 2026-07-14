from __future__ import annotations

from trading_agent.strategy_factory import StrategyMode, build_strategy


def test_strategy_factory_keeps_intraday_strategies_independent() -> None:
    orb = build_strategy(StrategyMode.ORB, range_minutes=5)
    reclaim = build_strategy(StrategyMode.VWAP_RECLAIM, range_minutes=5)
    hod_breakout = build_strategy(StrategyMode.HOD_BREAKOUT, range_minutes=5)
    gap_and_go = build_strategy(StrategyMode.GAP_AND_GO, range_minutes=5)

    assert orb.name == "opening_range_breakout"
    assert reclaim.name == "first_pullback_vwap_reclaim"
    assert hod_breakout.name == "first_hod_volume_breakout"
    assert gap_and_go.name == "five_minute_gap_hold"
