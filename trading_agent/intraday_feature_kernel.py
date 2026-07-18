"""Pure completed-minute-bar indicator kernel bound to research input identity."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_intraday_volume_profile_models import (
    IntradayVolumeProfileError,
    IntradayVolumeProfileEvidence,
    validate_intraday_volume_profile,
)

_ONE_MINUTE: Final = dt.timedelta(minutes=1)
_MAX_STALENESS: Final = dt.timedelta(minutes=2)
_MINIMUM_BARS: Final = 35
_ATR_PERIOD: Final = 14
_RSI_PERIOD: Final = 14
_MACD_FAST: Final = 12
_MACD_SLOW: Final = 26
_MACD_SIGNAL: Final = 9
_INDICATOR_SEMANTIC_VERSION: Final = "intraday_completed_minute_v2"


class FeatureSnapshotStatus(StrEnum):
    READY = "ready"
    BLOCKED_GAP = "blocked_gap"
    BLOCKED_STALE = "blocked_stale"
    BLOCKED_INSUFFICIENT_HISTORY = "blocked_insufficient_history"


@dataclass(frozen=True, slots=True)
class CompletedMinuteBar:
    start_at: dt.datetime
    end_at: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True, slots=True)
class IntradayFeatureSnapshot:
    identity: ResearchInputIdentity
    volume_profile: IntradayVolumeProfileEvidence
    instrument_id: str
    observed_at: dt.datetime
    status: FeatureSnapshotStatus
    source_start_at: dt.datetime | None
    source_end_at: dt.datetime | None
    bar_count: int
    indicator_semantic_version: str
    vwap: Decimal | None
    atr14: Decimal | None
    rsi14: Decimal | None
    macd_line: Decimal | None
    macd_signal: Decimal | None
    macd_histogram: Decimal | None
    rvol: Decimal | None
    breakout_close_above_prior_high: bool | None


def build_intraday_feature_snapshot(
    identity: ResearchInputIdentity,
    instrument_id: str,
    observed_at: dt.datetime,
    bars: Sequence[CompletedMinuteBar],
    volume_profile: IntradayVolumeProfileEvidence,
) -> IntradayFeatureSnapshot:
    if type(identity) is not ResearchInputIdentity:
        raise ValueError("invalid research input identity")
    if type(instrument_id) is not str or not instrument_id:
        raise ValueError("invalid instrument id")
    if type(observed_at) is not dt.datetime or not _aware(observed_at):
        raise ValueError("invalid observed_at")
    try:
        validate_intraday_volume_profile(volume_profile)
    except IntradayVolumeProfileError as error:
        raise ValueError("invalid intraday volume profile") from error
    if (
        volume_profile.instrument_id != instrument_id
        or volume_profile.target_session_date != observed_at.astimezone(NEW_YORK).date()
    ):
        raise ValueError("invalid intraday volume profile")

    bar_tuple = tuple(bars)
    bar_count = len(bar_tuple)
    source_start_at, source_end_at = _safe_source_range(bar_tuple)
    if bar_count > volume_profile.through_minute:
        raise ValueError("invalid intraday volume profile")

    if not _bars_are_valid_contiguous(bar_tuple):
        return _blocked(
            identity=identity,
            volume_profile=volume_profile,
            instrument_id=instrument_id,
            observed_at=observed_at,
            status=FeatureSnapshotStatus.BLOCKED_GAP,
            source_start_at=source_start_at,
            source_end_at=source_end_at,
            bar_count=bar_count,
        )

    if bar_count < max(_MINIMUM_BARS, volume_profile.through_minute):
        return _blocked(
            identity=identity,
            volume_profile=volume_profile,
            instrument_id=instrument_id,
            observed_at=observed_at,
            status=FeatureSnapshotStatus.BLOCKED_INSUFFICIENT_HISTORY,
            source_start_at=source_start_at,
            source_end_at=source_end_at,
            bar_count=bar_count,
        )

    latest_end = bar_tuple[-1].end_at
    age = observed_at - latest_end
    if latest_end >= observed_at or age > _MAX_STALENESS:
        return _blocked(
            identity=identity,
            volume_profile=volume_profile,
            instrument_id=instrument_id,
            observed_at=observed_at,
            status=FeatureSnapshotStatus.BLOCKED_STALE,
            source_start_at=source_start_at,
            source_end_at=source_end_at,
            bar_count=bar_count,
        )

    total_volume = sum((Decimal(bar.volume) for bar in bar_tuple), Decimal(0))
    if total_volume <= 0:
        return _blocked(
            identity=identity,
            volume_profile=volume_profile,
            instrument_id=instrument_id,
            observed_at=observed_at,
            status=FeatureSnapshotStatus.BLOCKED_INSUFFICIENT_HISTORY,
            source_start_at=source_start_at,
            source_end_at=source_end_at,
            bar_count=bar_count,
        )

    typical_price_volume = sum(
        (((bar.high + bar.low + bar.close) / Decimal(3)) * Decimal(bar.volume) for bar in bar_tuple),
        Decimal(0),
    )
    vwap = typical_price_volume / total_volume
    atr14 = _wilder_atr14(bar_tuple)
    rsi14 = _wilder_rsi14(bar_tuple)
    macd_line, macd_signal, macd_histogram = _macd_12_26_9(bar_tuple)
    rvol = total_volume / volume_profile.expected_cumulative_volume
    prior_high = max(bar.high for bar in bar_tuple[:-1])
    breakout = bar_tuple[-1].close > prior_high

    return IntradayFeatureSnapshot(
        identity=identity,
        volume_profile=volume_profile,
        instrument_id=instrument_id,
        observed_at=observed_at,
        status=FeatureSnapshotStatus.READY,
        source_start_at=source_start_at,
        source_end_at=source_end_at,
        bar_count=bar_count,
        indicator_semantic_version=_INDICATOR_SEMANTIC_VERSION,
        vwap=vwap,
        atr14=atr14,
        rsi14=rsi14,
        macd_line=macd_line,
        macd_signal=macd_signal,
        macd_histogram=macd_histogram,
        rvol=rvol,
        breakout_close_above_prior_high=breakout,
    )


def _blocked(
    *,
    identity: ResearchInputIdentity,
    volume_profile: IntradayVolumeProfileEvidence,
    instrument_id: str,
    observed_at: dt.datetime,
    status: FeatureSnapshotStatus,
    source_start_at: dt.datetime | None,
    source_end_at: dt.datetime | None,
    bar_count: int,
) -> IntradayFeatureSnapshot:
    return IntradayFeatureSnapshot(
        identity=identity,
        volume_profile=volume_profile,
        instrument_id=instrument_id,
        observed_at=observed_at,
        status=status,
        source_start_at=source_start_at,
        source_end_at=source_end_at,
        bar_count=bar_count,
        indicator_semantic_version=_INDICATOR_SEMANTIC_VERSION,
        vwap=None,
        atr14=None,
        rsi14=None,
        macd_line=None,
        macd_signal=None,
        macd_histogram=None,
        rvol=None,
        breakout_close_above_prior_high=None,
    )


def _aware(value: object) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


def _safe_aware_datetime(value: object) -> dt.datetime | None:
    if type(value) is not dt.datetime:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value


def _safe_source_range(
    bars: tuple[CompletedMinuteBar, ...],
) -> tuple[dt.datetime | None, dt.datetime | None]:
    if not bars:
        return None, None
    first = bars[0]
    last = bars[-1]
    start_at = _safe_aware_datetime(first.start_at) if type(first) is CompletedMinuteBar else None
    end_at = _safe_aware_datetime(last.end_at) if type(last) is CompletedMinuteBar else None
    return start_at, end_at


def _bars_are_valid_contiguous(bars: tuple[CompletedMinuteBar, ...]) -> bool:
    if not bars:
        return False
    previous: CompletedMinuteBar | None = None
    for bar in bars:
        if type(bar) is not CompletedMinuteBar:
            return False
        if not _aware(bar.start_at) or not _aware(bar.end_at):
            return False
        if bar.end_at - bar.start_at != _ONE_MINUTE:
            return False
        if not _valid_ohlcv(bar):
            return False
        if previous is not None:
            if bar.start_at != previous.end_at:
                return False
            if bar.start_at <= previous.start_at:
                return False
        previous = bar
    return True


def _valid_ohlcv(bar: CompletedMinuteBar) -> bool:
    prices = (bar.open, bar.high, bar.low, bar.close)
    if type(bar.volume) is not int or bar.volume < 0:
        return False
    if not all(type(price) is Decimal and price.is_finite() and price > 0 for price in prices):
        return False
    return bar.low <= min(bar.open, bar.close) and max(bar.open, bar.close) <= bar.high


def _true_ranges(bars: tuple[CompletedMinuteBar, ...]) -> list[Decimal]:
    ranges: list[Decimal] = []
    previous_close = bars[0].close
    for index, bar in enumerate(bars):
        if index == 0:
            ranges.append(bar.high - bar.low)
        else:
            ranges.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            )
        previous_close = bar.close
    return ranges


def _wilder_atr14(bars: tuple[CompletedMinuteBar, ...]) -> Decimal:
    true_ranges = _true_ranges(bars)
    # Seed on the first 14 completed true ranges after the opening bar.
    atr = sum(true_ranges[1 : _ATR_PERIOD + 1]) / Decimal(_ATR_PERIOD)
    for true_range in true_ranges[_ATR_PERIOD + 1 :]:
        atr = ((atr * Decimal(_ATR_PERIOD - 1)) + true_range) / Decimal(_ATR_PERIOD)
    return atr


def _wilder_rsi14(bars: tuple[CompletedMinuteBar, ...]) -> Decimal:
    changes = [bars[index].close - bars[index - 1].close for index in range(1, len(bars))]
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
    bars: tuple[CompletedMinuteBar, ...],
) -> tuple[Decimal, Decimal, Decimal]:
    closes = [bar.close for bar in bars]
    ema_fast = _ema_series(closes, _MACD_FAST)
    ema_slow = _ema_series(closes, _MACD_SLOW)
    macd_values: list[Decimal] = []
    for fast, slow in zip(ema_fast, ema_slow, strict=True):
        if fast is None or slow is None:
            continue
        macd_values.append(fast - slow)
    signal_series = _ema_series(macd_values, _MACD_SIGNAL)
    macd_line = macd_values[-1]
    macd_signal = signal_series[-1]
    if macd_signal is None:
        raise ValueError("macd signal unavailable")
    return macd_line, macd_signal, macd_line - macd_signal


__all__ = (
    "CompletedMinuteBar",
    "FeatureSnapshotStatus",
    "IntradayFeatureSnapshot",
    "build_intraday_feature_snapshot",
)
