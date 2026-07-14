from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from trading_agent.gap_strategy import (
    FiveMinuteGapHold,
    GapAndGoConfig,
    GapDriveClassification,
)
from trading_agent.models import BarInput, MomentumCandidate


def test_gap_and_go_emits_when_first_five_minutes_hold_and_drive_higher() -> None:
    strategy = FiveMinuteGapHold(GapAndGoConfig())
    bars = (
        _bar(30, 10.50, 10.70, 10.40, 10.65),
        _bar(31, 10.65, 10.75, 10.55, 10.70),
        _bar(32, 10.70, 10.80, 10.65, 10.75),
        _bar(33, 10.75, 10.78, 10.65, 10.72),
        _bar(34, 10.72, 10.90, 10.68, 10.85),
    )

    signals = tuple(
        signal
        for bar in bars
        for signal in (strategy.observe(bar, _candidate(bar)),)
        if signal is not None
    )

    assert len(signals) == 1
    assert signals[0].timestamp == bars[-1].timestamp + dt.timedelta(minutes=1)
    assert signals[0].entry > bars[-1].close
    assert signals[0].stop == 10.25
    assert "갭 유지" in signals[0].rationale
    assert strategy.classification("GAP", bars[-1].timestamp.date()) is (
        GapDriveClassification.CONTINUATION
    )


def test_gap_and_go_classifies_half_gap_loss_as_failure() -> None:
    strategy = FiveMinuteGapHold(GapAndGoConfig())
    bars = (
        _bar(30, 10.50, 10.60, 10.35, 10.45),
        _bar(31, 10.45, 10.50, 10.30, 10.35),
        _bar(32, 10.35, 10.40, 10.20, 10.25),
        _bar(33, 10.25, 10.32, 10.15, 10.20),
        _bar(34, 10.20, 10.25, 10.10, 10.15),
    )

    signals = tuple(
        signal
        for bar in bars
        for signal in (strategy.observe(bar, _candidate(bar)),)
        if signal is not None
    )

    assert signals == ()
    assert strategy.classification("GAP", bars[-1].timestamp.date()) is (
        GapDriveClassification.GAP_FAILURE
    )


def test_gap_and_go_does_not_backdate_when_candidate_arrives_after_decision() -> None:
    strategy = FiveMinuteGapHold(GapAndGoConfig())
    opening = (
        _bar(30, 10.50, 10.70, 10.40, 10.65),
        _bar(31, 10.65, 10.75, 10.55, 10.70),
        _bar(32, 10.70, 10.80, 10.65, 10.75),
        _bar(33, 10.75, 10.78, 10.65, 10.72),
        _bar(34, 10.72, 10.90, 10.68, 10.85),
    )
    signals = tuple(
        signal
        for bar in opening
        for signal in (strategy.observe(bar, None),)
        if signal is not None
    )
    late_bar = _bar(35, 10.85, 11.00, 10.80, 10.95)
    late_signal = strategy.observe(late_bar, _candidate(late_bar))

    assert signals == ()
    assert late_signal is None
    assert strategy.classification("GAP", late_bar.timestamp.date()) is (
        GapDriveClassification.NEUTRAL
    )


def _bar(
    minute: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> BarInput:
    return BarInput(
        "GAP",
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
        200,
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
        bar.close / bar.prior_close - 1.0,
        3.0,
        5_000_000.0,
        bar.spread_bps,
        bar.catalyst,
    )
