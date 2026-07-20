from __future__ import annotations

import datetime as dt
from dataclasses import fields
from decimal import Decimal

import pytest

from tests.intraday_feature_kernel_fixtures import (
    EXPECTED_VOLUME,
    INSTRUMENT_ID,
    UTC,
    BarSeriesSpec,
    bar,
    bars,
    custom_bars,
    identity,
)
from tests.us_volume_profile_fixtures import volume_profile
from trading_agent.intraday_feature_kernel import (
    CompletedMinuteBar,
    FeatureSnapshotStatus,
    IntradayFeatureSnapshot,
    build_intraday_feature_snapshot,
)


def test_completed_minute_bar_and_snapshot_are_frozen_slots() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    completed_bar = bar(start)
    completed_bars = bars(35, start)
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        completed_bars[-1].end_at + dt.timedelta(seconds=30),
        completed_bars,
        EXPECTED_VOLUME,
    )

    assert completed_bar.__slots__ == (
        "start_at",
        "end_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
    )
    assert tuple(field.name for field in fields(CompletedMinuteBar)) == completed_bar.__slots__
    assert tuple(field.name for field in fields(IntradayFeatureSnapshot)) == (
        "identity",
        "volume_profile",
        "instrument_id",
        "observed_at",
        "status",
        "source_start_at",
        "source_end_at",
        "bar_count",
        "indicator_semantic_version",
        "close",
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
        completed_bar.__setattr__("open", Decimal("1"))
    with pytest.raises((AttributeError, TypeError)):
        snapshot.__setattr__("status", FeatureSnapshotStatus.BLOCKED_GAP)


def test_ready_snapshot_is_deterministic_and_byte_stable() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    closes = tuple(str(Decimal("100") + Decimal(index) * Decimal("0.25")) for index in range(35))
    highs = (
        *(str(Decimal("100") + Decimal(index) * Decimal("0.25") + Decimal("0.10")) for index in range(34)),
        str(Decimal("100") + Decimal(34) * Decimal("0.25") + Decimal("1.00")),
    )
    closes = (*closes[:-1], str(Decimal(highs[-2]) + Decimal("0.50")))
    completed_bars = custom_bars(BarSeriesSpec(35, start, closes=closes, highs=highs))
    observed_at = completed_bars[-1].end_at + dt.timedelta(seconds=45)
    input_identity = identity()

    first = build_intraday_feature_snapshot(
        input_identity,
        INSTRUMENT_ID,
        observed_at,
        completed_bars,
        EXPECTED_VOLUME,
    )
    second = build_intraday_feature_snapshot(
        input_identity,
        INSTRUMENT_ID,
        observed_at,
        completed_bars,
        EXPECTED_VOLUME,
    )

    assert first.status is FeatureSnapshotStatus.READY
    assert first == second
    assert first.identity == input_identity
    assert first.source_start_at == completed_bars[0].start_at
    assert first.source_end_at == completed_bars[-1].end_at
    assert first.indicator_semantic_version == "intraday_completed_minute_v2"
    assert first.close == completed_bars[-1].close
    assert all(
        isinstance(value, Decimal)
        for value in (
            first.vwap,
            first.atr14,
            first.rsi14,
            first.macd_line,
            first.macd_signal,
            first.macd_histogram,
            first.rvol,
        )
    )
    assert first.breakout_close_above_prior_high is True


def test_ready_vwap_rvol_and_breakout_match_closed_form() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    completed_bars = custom_bars(
        BarSeriesSpec(
            35,
            start,
            closes=("100",) * 34 + ("110",),
            highs=("101",) * 34 + ("111",),
            lows=("99",) * 35,
            volumes=(100,) * 35,
        )
    )
    expected = volume_profile(
        INSTRUMENT_ID,
        dt.date(2026, 7, 17),
        expected_cumulative_volume=3_500,
    )
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        completed_bars[-1].end_at + dt.timedelta(minutes=1),
        completed_bars,
        expected,
    )

    typical_pv = sum(
        ((item.high + item.low + item.close) / Decimal(3)) * Decimal(item.volume) for item in completed_bars
    )
    total_volume = sum(Decimal(item.volume) for item in completed_bars)
    assert snapshot.status is FeatureSnapshotStatus.READY
    assert snapshot.vwap == typical_pv / total_volume
    assert snapshot.rvol == total_volume / expected.expected_cumulative_volume
    assert snapshot.breakout_close_above_prior_high is True
