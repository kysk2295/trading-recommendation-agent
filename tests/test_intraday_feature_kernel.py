from __future__ import annotations

import datetime as dt
from dataclasses import fields
from decimal import Decimal

import pytest

from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.intraday_feature_kernel import (
    CompletedMinuteBar,
    FeatureSnapshotStatus,
    IntradayFeatureSnapshot,
    build_intraday_feature_snapshot,
)
from trading_agent.research_input_identity import ResearchInputIdentity

_UTC = dt.UTC
_SCOPE = "us_equities.day_trading.orb"
_INSTRUMENT_ID = "us-eq-fixture-aapl"
_EXPECTED_VOLUME = Decimal("10000")
_INDICATOR_NONE_FIELDS = (
    "vwap",
    "atr14",
    "rsi14",
    "macd_line",
    "macd_signal",
    "macd_histogram",
    "rvol",
    "breakout_close_above_prior_high",
)


def _identity() -> ResearchInputIdentity:
    replay = CanonicalDatasetReplay(
        dataset_id="ds_fixture",
        event_count=1,
        canonical_event_content_sha256="a" * 64,
        parquet_sha256="c" * 64,
        raw_manifest_id="raw_manifest_fixture",
        raw_manifest_content_sha256="b" * 64,
    )
    return ResearchInputIdentity.from_verified_replay(_SCOPE, replay)


def _bar(
    start: dt.datetime,
    *,
    open_: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100.5",
    volume: int = 100,
) -> CompletedMinuteBar:
    return CompletedMinuteBar(
        start_at=start,
        end_at=start + dt.timedelta(minutes=1),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


def _bars(
    count: int,
    *,
    start: dt.datetime,
    closes: list[str] | None = None,
    highs: list[str] | None = None,
    lows: list[str] | None = None,
    volumes: list[int] | None = None,
) -> tuple[CompletedMinuteBar, ...]:
    bars: list[CompletedMinuteBar] = []
    for index in range(count):
        close = (
            closes[index]
            if closes is not None
            else str(Decimal("100") + (Decimal(index % 5) * Decimal("0.1")))
        )
        high = highs[index] if highs is not None else None
        low = lows[index] if lows is not None else None
        volume = volumes[index] if volumes is not None else 100 + index
        close_dec = Decimal(close)
        high_dec = Decimal(high) if high is not None else close_dec + Decimal("0.5")
        low_dec = Decimal(low) if low is not None else close_dec - Decimal("0.5")
        open_dec = close_dec
        bars.append(
            CompletedMinuteBar(
                start_at=start + dt.timedelta(minutes=index),
                end_at=start + dt.timedelta(minutes=index + 1),
                open=open_dec,
                high=high_dec,
                low=low_dec,
                close=close_dec,
                volume=volume,
            )
        )
    return tuple(bars)


def _assert_blocked_indicators(snapshot: IntradayFeatureSnapshot) -> None:
    for field_name in _INDICATOR_NONE_FIELDS:
        assert getattr(snapshot, field_name) is None


def test_completed_minute_bar_and_snapshot_are_frozen_slots() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    bar = _bar(start)
    identity = _identity()
    bars = _bars(35, start=start)
    observed_at = bars[-1].end_at + dt.timedelta(seconds=30)

    snapshot = build_intraday_feature_snapshot(
        identity,
        _INSTRUMENT_ID,
        observed_at,
        bars,
        _EXPECTED_VOLUME,
    )

    assert bar.__slots__ == (
        "start_at",
        "end_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
    )
    assert tuple(field.name for field in fields(CompletedMinuteBar)) == (
        "start_at",
        "end_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
    )
    assert tuple(field.name for field in fields(IntradayFeatureSnapshot)) == (
        "identity",
        "instrument_id",
        "observed_at",
        "status",
        "source_start_at",
        "source_end_at",
        "bar_count",
        "indicator_semantic_version",
        "vwap",
        "atr14",
        "rsi14",
        "macd_line",
        "macd_signal",
        "macd_histogram",
        "rvol",
        "breakout_close_above_prior_high",
    )
    with pytest.raises((AttributeError, TypeError)):
        bar.open = Decimal("1")  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        snapshot.status = FeatureSnapshotStatus.BLOCKED_GAP  # type: ignore[misc]


def test_ready_snapshot_is_deterministic_and_byte_stable() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    closes = [str(Decimal("100") + Decimal(index) * Decimal("0.25")) for index in range(35)]
    # Force a clear prior-high breakout on the final bar.
    highs = [str(Decimal("100") + Decimal(index) * Decimal("0.25") + Decimal("0.10")) for index in range(34)]
    highs.append(str(Decimal("100") + Decimal(34) * Decimal("0.25") + Decimal("1.00")))
    closes[-1] = str(Decimal(highs[-2]) + Decimal("0.50"))
    bars = _bars(35, start=start, closes=closes, highs=highs)
    observed_at = bars[-1].end_at + dt.timedelta(seconds=45)
    identity = _identity()

    first = build_intraday_feature_snapshot(
        identity,
        _INSTRUMENT_ID,
        observed_at,
        bars,
        _EXPECTED_VOLUME,
    )
    second = build_intraday_feature_snapshot(
        identity,
        _INSTRUMENT_ID,
        observed_at,
        bars,
        _EXPECTED_VOLUME,
    )

    assert first.status is FeatureSnapshotStatus.READY
    assert first == second
    assert first.identity == identity
    assert first.instrument_id == _INSTRUMENT_ID
    assert first.observed_at == observed_at
    assert first.source_start_at == bars[0].start_at
    assert first.source_end_at == bars[-1].end_at
    assert first.bar_count == 35
    assert first.indicator_semantic_version == "intraday_completed_minute_v1"
    assert isinstance(first.vwap, Decimal)
    assert isinstance(first.atr14, Decimal)
    assert isinstance(first.rsi14, Decimal)
    assert isinstance(first.macd_line, Decimal)
    assert isinstance(first.macd_signal, Decimal)
    assert isinstance(first.macd_histogram, Decimal)
    assert isinstance(first.rvol, Decimal)
    assert first.breakout_close_above_prior_high is True
    assert first.vwap == second.vwap
    assert first.atr14 == second.atr14
    assert first.rsi14 == second.rsi14
    assert first.macd_line == second.macd_line
    assert first.macd_signal == second.macd_signal
    assert first.macd_histogram == second.macd_histogram
    assert first.rvol == second.rvol


def test_ready_vwap_rvol_and_breakout_match_closed_form() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    bars = _bars(
        35,
        start=start,
        closes=["100"] * 34 + ["110"],
        highs=["101"] * 34 + ["111"],
        lows=["99"] * 35,
        volumes=[100] * 35,
    )
    observed_at = bars[-1].end_at + dt.timedelta(minutes=1)
    expected = Decimal("3500")

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        bars,
        expected,
    )

    typical_pv = sum(
        ((bar.high + bar.low + bar.close) / Decimal(3)) * Decimal(bar.volume) for bar in bars
    )
    total_volume = sum(Decimal(bar.volume) for bar in bars)
    assert snapshot.status is FeatureSnapshotStatus.READY
    assert snapshot.vwap == typical_pv / total_volume
    assert snapshot.rvol == total_volume / expected
    assert snapshot.breakout_close_above_prior_high is True


def test_ready_wilder_atr14_matches_manual_seed_and_smooth() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    # Flat path then one expansion bar so TR sequence is simple.
    closes = ["100"] * 35
    highs = ["101"] * 34 + ["105"]
    lows = ["99"] * 34 + ["98"]
    bars = _bars(35, start=start, closes=closes, highs=highs, lows=lows)
    observed_at = bars[-1].end_at + dt.timedelta(seconds=30)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        bars,
        _EXPECTED_VOLUME,
    )

    true_ranges: list[Decimal] = []
    previous_close = bars[0].close
    for index, bar in enumerate(bars):
        if index == 0:
            true_ranges.append(bar.high - bar.low)
        else:
            true_ranges.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            )
        previous_close = bar.close
    atr = sum(true_ranges[1:15]) / Decimal(14)
    for true_range in true_ranges[15:]:
        atr = ((atr * Decimal(13)) + true_range) / Decimal(14)

    assert snapshot.status is FeatureSnapshotStatus.READY
    assert snapshot.atr14 == atr


def test_ready_wilder_rsi14_matches_manual_seed_and_smooth() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    closes = [str(Decimal("100") + Decimal(index)) for index in range(20)] + [
        str(Decimal("119") - Decimal(index)) for index in range(15)
    ]
    bars = _bars(35, start=start, closes=closes)
    observed_at = bars[-1].end_at + dt.timedelta(seconds=30)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        bars,
        _EXPECTED_VOLUME,
    )

    changes = [bars[index].close - bars[index - 1].close for index in range(1, len(bars))]
    gains = [change if change > 0 else Decimal(0) for change in changes]
    losses = [-change if change < 0 else Decimal(0) for change in changes]
    avg_gain = sum(gains[:14]) / Decimal(14)
    avg_loss = sum(losses[:14]) / Decimal(14)
    for gain, loss in zip(gains[14:], losses[14:], strict=True):
        avg_gain = ((avg_gain * Decimal(13)) + gain) / Decimal(14)
        avg_loss = ((avg_loss * Decimal(13)) + loss) / Decimal(14)
    if avg_loss == 0:
        expected_rsi = Decimal(100)
    else:
        rs = avg_gain / avg_loss
        expected_rsi = Decimal(100) - (Decimal(100) / (Decimal(1) + rs))

    assert snapshot.status is FeatureSnapshotStatus.READY
    assert snapshot.rsi14 == expected_rsi


def test_ready_macd_matches_sma_seeded_ema_12_26_9() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    closes = [str(Decimal("100") + (Decimal(index) * Decimal("0.5"))) for index in range(35)]
    bars = _bars(35, start=start, closes=closes)
    observed_at = bars[-1].end_at + dt.timedelta(seconds=30)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        bars,
        _EXPECTED_VOLUME,
    )

    closes_dec = [bar.close for bar in bars]

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

    ema12 = ema(closes_dec, 12)
    ema26 = ema(closes_dec, 26)
    macd_line_series: list[Decimal | None] = [
        (fast - slow) if fast is not None and slow is not None else None
        for fast, slow in zip(ema12, ema26, strict=True)
    ]
    macd_values = [value for value in macd_line_series if value is not None]
    signal_series = ema(macd_values, 9)
    macd_line = macd_values[-1]
    macd_signal = signal_series[-1]
    assert macd_signal is not None
    macd_histogram = macd_line - macd_signal

    assert snapshot.status is FeatureSnapshotStatus.READY
    assert snapshot.macd_line == macd_line
    assert snapshot.macd_signal == macd_signal
    assert snapshot.macd_histogram == macd_histogram


def test_insufficient_history_blocks_all_indicators() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    bars = _bars(34, start=start)
    observed_at = bars[-1].end_at + dt.timedelta(seconds=30)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        bars,
        _EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_INSUFFICIENT_HISTORY
    assert snapshot.bar_count == 34
    assert snapshot.source_start_at == bars[0].start_at
    assert snapshot.source_end_at == bars[-1].end_at
    _assert_blocked_indicators(snapshot)


def test_non_positive_expected_volume_blocks_as_insufficient_history() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    bars = _bars(35, start=start)
    observed_at = bars[-1].end_at + dt.timedelta(seconds=30)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        bars,
        Decimal("0"),
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_INSUFFICIENT_HISTORY
    _assert_blocked_indicators(snapshot)


def test_gap_blocks_all_indicators() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    bars = list(_bars(35, start=start))
    # Drop contiguity between bar 10 and 11 by shifting the tail one extra minute.
    shifted = []
    for index, bar in enumerate(bars):
        if index < 11:
            shifted.append(bar)
        else:
            shifted.append(
                CompletedMinuteBar(
                    start_at=bar.start_at + dt.timedelta(minutes=1),
                    end_at=bar.end_at + dt.timedelta(minutes=1),
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                )
            )
    observed_at = shifted[-1].end_at + dt.timedelta(seconds=30)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        tuple(shifted),
        _EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_GAP
    _assert_blocked_indicators(snapshot)


def test_stale_latest_bar_blocks_all_indicators() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    bars = _bars(35, start=start)
    observed_at = bars[-1].end_at + dt.timedelta(minutes=2, microseconds=1)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        bars,
        _EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_STALE
    _assert_blocked_indicators(snapshot)


def test_latest_bar_not_strictly_before_observed_at_is_stale() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    bars = _bars(35, start=start)
    observed_at = bars[-1].end_at

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        bars,
        _EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_STALE
    _assert_blocked_indicators(snapshot)


def test_exactly_two_minute_age_is_still_ready() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    bars = _bars(35, start=start)
    observed_at = bars[-1].end_at + dt.timedelta(minutes=2)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        bars,
        _EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.READY
    assert snapshot.vwap is not None


def test_naive_observed_at_raises() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    bars = _bars(35, start=start)

    with pytest.raises(ValueError):
        build_intraday_feature_snapshot(
            _identity(),
            _INSTRUMENT_ID,
            dt.datetime(2026, 7, 17, 14, 40),
            bars,
            _EXPECTED_VOLUME,
        )


def test_naive_bar_timestamps_are_treated_as_gap() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0)
    bars = tuple(
        CompletedMinuteBar(
            start_at=start + dt.timedelta(minutes=index),
            end_at=start + dt.timedelta(minutes=index + 1),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=100,
        )
        for index in range(35)
    )
    observed_at = dt.datetime(2026, 7, 17, 14, 40, tzinfo=_UTC)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        bars,
        _EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_GAP
    _assert_blocked_indicators(snapshot)


def test_non_breakout_when_close_equals_prior_high() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    prior_high = "105"
    bars = _bars(
        35,
        start=start,
        closes=["100"] * 34 + [prior_high],
        highs=[prior_high] * 34 + [prior_high],
        lows=["99"] * 35,
    )
    observed_at = bars[-1].end_at + dt.timedelta(seconds=30)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        bars,
        _EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.READY
    assert snapshot.breakout_close_above_prior_high is False


def test_malformed_bar_timestamp_type_blocks_gap_without_exception() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    bars = list(_bars(35, start=start))
    bars[0] = CompletedMinuteBar(
        start_at="2026-07-17T14:00:00+00:00",  # type: ignore[arg-type]
        end_at=bars[0].end_at,
        open=bars[0].open,
        high=bars[0].high,
        low=bars[0].low,
        close=bars[0].close,
        volume=bars[0].volume,
    )
    observed_at = bars[-1].end_at + dt.timedelta(seconds=30)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        tuple(bars),
        _EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_GAP
    assert snapshot.source_start_at is None
    assert snapshot.source_end_at == bars[-1].end_at
    assert snapshot.bar_count == 35
    _assert_blocked_indicators(snapshot)


def test_malformed_bar_end_timestamp_type_blocks_gap_without_exception() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    bars = list(_bars(35, start=start))
    bars[-1] = CompletedMinuteBar(
        start_at=bars[-1].start_at,
        end_at=1_721_234_567,  # type: ignore[arg-type]
        open=bars[-1].open,
        high=bars[-1].high,
        low=bars[-1].low,
        close=bars[-1].close,
        volume=bars[-1].volume,
    )
    observed_at = start + dt.timedelta(minutes=36)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        tuple(bars),
        _EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_GAP
    assert snapshot.source_start_at == bars[0].start_at
    assert snapshot.source_end_at is None
    _assert_blocked_indicators(snapshot)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("open", "100"),
        ("high", 101.0),
        ("low", None),
        ("close", 100),
        ("volume", "100"),
        ("volume", -1),
        ("volume", True),
    ),
)
def test_malformed_ohlc_or_volume_blocks_gap_without_exception(
    field_name: str,
    field_value: object,
) -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)
    bars = list(_bars(35, start=start))
    base = bars[5]
    payload = {
        "start_at": base.start_at,
        "end_at": base.end_at,
        "open": base.open,
        "high": base.high,
        "low": base.low,
        "close": base.close,
        "volume": base.volume,
    }
    payload[field_name] = field_value
    bars[5] = CompletedMinuteBar(**payload)  # type: ignore[arg-type]
    observed_at = bars[-1].end_at + dt.timedelta(seconds=30)

    snapshot = build_intraday_feature_snapshot(
        _identity(),
        _INSTRUMENT_ID,
        observed_at,
        tuple(bars),
        _EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_GAP
    assert snapshot.source_start_at == bars[0].start_at
    assert snapshot.source_end_at == bars[-1].end_at
    assert snapshot.bar_count == 35
    _assert_blocked_indicators(snapshot)
