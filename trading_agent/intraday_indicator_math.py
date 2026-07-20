"""Pure indicator calculations over completed intraday price points."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

_ATR_PERIOD: Final = 14
_RSI_PERIOD: Final = 14
_MACD_FAST: Final = 12
_MACD_SLOW: Final = 26
_MACD_SIGNAL: Final = 9


@dataclass(frozen=True, slots=True)
class IntradayPricePoint:
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class IntradayIndicatorValues:
    atr14: Decimal
    rsi14: Decimal
    macd_line: Decimal
    macd_signal: Decimal
    macd_histogram: Decimal


def calculate_intraday_indicators(
    points: tuple[IntradayPricePoint, ...],
) -> IntradayIndicatorValues:
    atr14 = _wilder_atr14(points)
    rsi14 = _wilder_rsi14(points)
    macd_line, macd_signal, macd_histogram = _macd_12_26_9(points)
    return IntradayIndicatorValues(
        atr14=atr14,
        rsi14=rsi14,
        macd_line=macd_line,
        macd_signal=macd_signal,
        macd_histogram=macd_histogram,
    )


def _true_ranges(points: tuple[IntradayPricePoint, ...]) -> list[Decimal]:
    ranges: list[Decimal] = []
    previous_close = points[0].close
    for index, point in enumerate(points):
        if index == 0:
            ranges.append(point.high - point.low)
        else:
            ranges.append(
                max(
                    point.high - point.low,
                    abs(point.high - previous_close),
                    abs(point.low - previous_close),
                )
            )
        previous_close = point.close
    return ranges


def _wilder_atr14(points: tuple[IntradayPricePoint, ...]) -> Decimal:
    true_ranges = _true_ranges(points)
    # Seed on the first 14 completed true ranges after the opening bar.
    atr = sum(true_ranges[1 : _ATR_PERIOD + 1]) / Decimal(_ATR_PERIOD)
    for true_range in true_ranges[_ATR_PERIOD + 1 :]:
        atr = ((atr * Decimal(_ATR_PERIOD - 1)) + true_range) / Decimal(_ATR_PERIOD)
    return atr


def _wilder_rsi14(points: tuple[IntradayPricePoint, ...]) -> Decimal:
    changes = [
        points[index].close - points[index - 1].close for index in range(1, len(points))
    ]
    gains = [change if change > 0 else Decimal(0) for change in changes]
    losses = [-change if change < 0 else Decimal(0) for change in changes]
    avg_gain = sum(gains[:_RSI_PERIOD]) / Decimal(_RSI_PERIOD)
    avg_loss = sum(losses[:_RSI_PERIOD]) / Decimal(_RSI_PERIOD)
    for gain, loss in zip(gains[_RSI_PERIOD:], losses[_RSI_PERIOD:], strict=True):
        avg_gain = ((avg_gain * Decimal(_RSI_PERIOD - 1)) + gain) / Decimal(_RSI_PERIOD)
        avg_loss = ((avg_loss * Decimal(_RSI_PERIOD - 1)) + loss) / Decimal(_RSI_PERIOD)
    if avg_loss == 0:
        return Decimal(100)
    rs = avg_gain / avg_loss
    return Decimal(100) - (Decimal(100) / (Decimal(1) + rs))


def _ema_series(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    output: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return output
    seed = sum(values[:period], Decimal(0)) / Decimal(period)
    output[period - 1] = seed
    multiplier = Decimal(2) / Decimal(period + 1)
    previous = seed
    for index in range(period, len(values)):
        previous = ((values[index] - previous) * multiplier) + previous
        output[index] = previous
    return output


def _macd_12_26_9(
    points: tuple[IntradayPricePoint, ...],
) -> tuple[Decimal, Decimal, Decimal]:
    closes = [point.close for point in points]
    ema_fast = _ema_series(closes, _MACD_FAST)
    ema_slow = _ema_series(closes, _MACD_SLOW)
    macd_values = [
        fast - slow
        for fast, slow in zip(ema_fast, ema_slow, strict=True)
        if fast is not None and slow is not None
    ]
    signal_series = _ema_series(macd_values, _MACD_SIGNAL)
    macd_line = macd_values[-1]
    macd_signal = signal_series[-1]
    if macd_signal is None:
        raise RuntimeError
    return macd_line, macd_signal, macd_line - macd_signal


__all__ = (
    "IntradayIndicatorValues",
    "IntradayPricePoint",
    "calculate_intraday_indicators",
)
