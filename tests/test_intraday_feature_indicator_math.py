from __future__ import annotations

import datetime as dt
from decimal import Decimal

from tests.intraday_feature_kernel_fixtures import (
    EXPECTED_VOLUME,
    INSTRUMENT_ID,
    UTC,
    BarSeriesSpec,
    custom_bars,
    identity,
)
from trading_agent.intraday_feature_kernel import (
    FeatureSnapshotStatus,
    build_intraday_feature_snapshot,
)


def test_ready_wilder_atr14_matches_manual_seed_and_smooth() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    completed_bars = custom_bars(
        BarSeriesSpec(
            35,
            start,
            closes=("100",) * 35,
            highs=("101",) * 34 + ("105",),
            lows=("99",) * 34 + ("98",),
        )
    )
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        completed_bars[-1].end_at + dt.timedelta(seconds=30),
        completed_bars,
        EXPECTED_VOLUME,
    )

    true_ranges: list[Decimal] = []
    previous_close = completed_bars[0].close
    for index, item in enumerate(completed_bars):
        if index == 0:
            true_ranges.append(item.high - item.low)
        else:
            true_ranges.append(
                max(
                    item.high - item.low,
                    abs(item.high - previous_close),
                    abs(item.low - previous_close),
                )
            )
        previous_close = item.close
    atr = sum(true_ranges[1:15]) / Decimal(14)
    for true_range in true_ranges[15:]:
        atr = ((atr * Decimal(13)) + true_range) / Decimal(14)

    assert snapshot.status is FeatureSnapshotStatus.READY
    assert snapshot.atr14 == atr


def test_ready_wilder_rsi14_matches_manual_seed_and_smooth() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    closes = tuple(str(Decimal("100") + Decimal(index)) for index in range(20)) + tuple(
        str(Decimal("119") - Decimal(index)) for index in range(15)
    )
    completed_bars = custom_bars(BarSeriesSpec(35, start, closes=closes))
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        completed_bars[-1].end_at + dt.timedelta(seconds=30),
        completed_bars,
        EXPECTED_VOLUME,
    )

    changes = [
        completed_bars[index].close - completed_bars[index - 1].close
        for index in range(1, len(completed_bars))
    ]
    gains = [change if change > 0 else Decimal(0) for change in changes]
    losses = [-change if change < 0 else Decimal(0) for change in changes]
    avg_gain = sum(gains[:14]) / Decimal(14)
    avg_loss = sum(losses[:14]) / Decimal(14)
    for gain, loss in zip(gains[14:], losses[14:], strict=True):
        avg_gain = ((avg_gain * Decimal(13)) + gain) / Decimal(14)
        avg_loss = ((avg_loss * Decimal(13)) + loss) / Decimal(14)
    expected_rsi = Decimal(100)
    if avg_loss != 0:
        expected_rsi = Decimal(100) - (
            Decimal(100) / (Decimal(1) + (avg_gain / avg_loss))
        )

    assert snapshot.status is FeatureSnapshotStatus.READY
    assert snapshot.rsi14 == expected_rsi


def test_ready_macd_matches_sma_seeded_ema_12_26_9() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    closes = tuple(
        str(Decimal("100") + (Decimal(index) * Decimal("0.5"))) for index in range(35)
    )
    completed_bars = custom_bars(BarSeriesSpec(35, start, closes=closes))
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        completed_bars[-1].end_at + dt.timedelta(seconds=30),
        completed_bars,
        EXPECTED_VOLUME,
    )

    def ema(values: list[Decimal], period: int) -> list[Decimal | None]:
        output: list[Decimal | None] = [None] * len(values)
        seed = sum(values[:period]) / Decimal(period)
        output[period - 1] = seed
        multiplier = Decimal(2) / Decimal(period + 1)
        previous = seed
        for index in range(period, len(values)):
            previous = ((values[index] - previous) * multiplier) + previous
            output[index] = previous
        return output

    closes_dec = [item.close for item in completed_bars]
    ema12 = ema(closes_dec, 12)
    ema26 = ema(closes_dec, 26)
    macd_values = [
        fast - slow
        for fast, slow in zip(ema12, ema26, strict=True)
        if fast is not None and slow is not None
    ]
    signal_series = ema(macd_values, 9)
    macd_signal = signal_series[-1]
    assert macd_signal is not None

    assert snapshot.status is FeatureSnapshotStatus.READY
    assert snapshot.macd_line == macd_values[-1]
    assert snapshot.macd_signal == macd_signal
    assert snapshot.macd_histogram == macd_values[-1] - macd_signal
