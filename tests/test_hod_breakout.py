from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from trading_agent.hod_strategy import FirstHodVolumeBreakout, HodBreakoutConfig
from trading_agent.models import BarInput, MomentumCandidate


def test_first_hod_breakout_emits_after_base_and_volume_expansion() -> None:
    strategy = FirstHodVolumeBreakout(
        HodBreakoutConfig(
            min_hod_gain_pct=0.03,
            breakout_buffer_bps=5.0,
            volume_multiplier=1.5,
            min_base_bars=2,
            max_base_bars=8,
            max_pullback_pct=0.03,
        )
    )
    bars = (
        _bar(30, 10.00, 10.20, 9.98, 10.15, 100),
        _bar(31, 10.15, 10.45, 10.10, 10.40, 200),
        _bar(32, 10.35, 10.42, 10.25, 10.30, 100),
        _bar(33, 10.30, 10.40, 10.20, 10.35, 100),
        _bar(34, 10.36, 10.55, 10.34, 10.52, 180),
    )

    signals = tuple(
        signal
        for bar in bars
        for signal in (strategy.observe(bar, _candidate(bar)),)
        if signal is not None
    )

    assert len(signals) == 1
    assert signals[0].timestamp == bars[-1].timestamp + dt.timedelta(minutes=1)
    assert signals[0].entry > bars[-1].high
    assert signals[0].stop == min(bars[-3].low, bars[-2].low)
    assert "HOD" in signals[0].rationale


def test_first_hod_breakout_rejects_low_volume_attempt_and_later_breakout() -> None:
    strategy = FirstHodVolumeBreakout(
        HodBreakoutConfig(
            min_hod_gain_pct=0.03,
            breakout_buffer_bps=5.0,
            volume_multiplier=1.5,
            min_base_bars=2,
            max_base_bars=8,
            max_pullback_pct=0.03,
        )
    )
    bars = (
        _bar(30, 10.00, 10.40, 9.98, 10.35, 200),
        _bar(31, 10.30, 10.38, 10.20, 10.25, 100),
        _bar(32, 10.25, 10.35, 10.15, 10.30, 100),
        _bar(33, 10.31, 10.50, 10.30, 10.45, 120),
        _bar(34, 10.44, 10.70, 10.40, 10.65, 300),
    )

    signals = tuple(
        signal
        for bar in bars
        for signal in (strategy.observe(bar, _candidate(bar)),)
        if signal is not None
    )

    assert signals == ()


def _bar(
    minute: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: int,
) -> BarInput:
    return BarInput(
        "HOD",
        dt.datetime(
            2026,
            7,
            10,
            9,
            minute,
            tzinfo=ZoneInfo("America/New_York"),
        ),
        open_price,
        high,
        low,
        close,
        volume,
        10.0,
        100_000,
        20.0,
        "KIS current ranking",
    )


def _candidate(bar: BarInput) -> MomentumCandidate:
    return MomentumCandidate(
        bar.symbol,
        bar.timestamp,
        bar.close,
        0.05,
        0.08,
        3.0,
        5_000_000.0,
        bar.spread_bps,
        bar.catalyst,
    )
