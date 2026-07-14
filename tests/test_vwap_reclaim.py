from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from trading_agent.models import BarInput, MomentumCandidate
from trading_agent.vwap_strategy import FirstPullbackVwapReclaim, VwapReclaimConfig


def test_first_pullback_vwap_reclaim_emits_after_impulse_touch_and_trigger() -> None:
    strategy = FirstPullbackVwapReclaim(
        VwapReclaimConfig(
            min_extension_pct=0.01,
            touch_tolerance_bps=20.0,
            reclaim_buffer_bps=5.0,
            volume_multiplier=1.2,
            max_reclaim_bars=5,
        )
    )
    bars = (
        _bar(30, 10.00, 10.05, 9.95, 10.00, 100),
        _bar(31, 10.00, 10.06, 9.96, 10.01, 100),
        _bar(32, 10.02, 10.35, 10.00, 10.30, 200),
        _bar(33, 10.20, 10.22, 10.05, 10.10, 100),
        _bar(34, 10.10, 10.36, 10.08, 10.32, 150),
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
    assert signals[0].stop == bars[-2].low
    assert "VWAP" in signals[0].rationale


def test_failed_first_pullback_does_not_select_a_later_reclaim() -> None:
    strategy = FirstPullbackVwapReclaim(
        VwapReclaimConfig(
            min_extension_pct=0.01,
            touch_tolerance_bps=20.0,
            reclaim_buffer_bps=5.0,
            volume_multiplier=1.0,
            max_reclaim_bars=5,
        )
    )
    bars = (
        _bar(30, 10.00, 10.05, 9.95, 10.00, 100),
        _bar(31, 10.00, 10.35, 10.00, 10.30, 200),
        _bar(32, 10.20, 10.22, 10.05, 10.10, 100),
        _bar(33, 10.08, 10.10, 9.70, 9.75, 200),
        _bar(34, 9.80, 10.40, 9.75, 10.35, 300),
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
        "VWAP",
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
        9.5,
        100_000,
        20.0,
        "KIS 현재 랭킹",
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
