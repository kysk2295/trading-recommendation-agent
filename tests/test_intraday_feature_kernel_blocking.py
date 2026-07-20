from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from tests.intraday_feature_kernel_fixtures import (
    EXPECTED_VOLUME,
    INSTRUMENT_ID,
    UTC,
    BarSeriesSpec,
    assert_blocked_indicators,
    bars,
    custom_bars,
    identity,
)
from trading_agent.intraday_feature_kernel import (
    FeatureSnapshotStatus,
    InvalidIntradayFeatureInputError,
    build_intraday_feature_snapshot,
)


def test_insufficient_history_blocks_all_indicators() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    completed_bars = bars(34, start)
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        completed_bars[-1].end_at + dt.timedelta(seconds=30),
        completed_bars,
        EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_INSUFFICIENT_HISTORY
    assert snapshot.bar_count == 34
    assert_blocked_indicators(snapshot)


def test_tampered_volume_profile_raises_before_feature_calculation() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    completed_bars = bars(35, start)
    tampered = replace(EXPECTED_VOLUME, expected_cumulative_volume=Decimal("0"))

    with pytest.raises(InvalidIntradayFeatureInputError):
        build_intraday_feature_snapshot(
            identity(),
            INSTRUMENT_ID,
            completed_bars[-1].end_at + dt.timedelta(seconds=30),
            completed_bars,
            tampered,
        )


def test_gap_blocks_all_indicators() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    shifted = tuple(
        item
        if index < 11
        else replace(
            item,
            start_at=item.start_at + dt.timedelta(minutes=1),
            end_at=item.end_at + dt.timedelta(minutes=1),
        )
        for index, item in enumerate(bars(35, start))
    )
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        shifted[-1].end_at + dt.timedelta(seconds=30),
        shifted,
        EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_GAP
    assert_blocked_indicators(snapshot)


@pytest.mark.parametrize(
    ("age", "expected_status"),
    (
        (dt.timedelta(minutes=2, microseconds=1), FeatureSnapshotStatus.BLOCKED_STALE),
        (dt.timedelta(0), FeatureSnapshotStatus.BLOCKED_STALE),
        (dt.timedelta(minutes=2), FeatureSnapshotStatus.READY),
    ),
)
def test_latest_bar_age_boundary(
    age: dt.timedelta,
    expected_status: FeatureSnapshotStatus,
) -> None:
    completed_bars = bars(35, dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC))
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        completed_bars[-1].end_at + age,
        completed_bars,
        EXPECTED_VOLUME,
    )

    assert snapshot.status is expected_status


def test_naive_observed_at_raises() -> None:
    completed_bars = bars(35, dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC))

    with pytest.raises(InvalidIntradayFeatureInputError):
        build_intraday_feature_snapshot(
            identity(),
            INSTRUMENT_ID,
            dt.datetime(2026, 7, 17, 14, 40),
            completed_bars,
            EXPECTED_VOLUME,
        )


def test_naive_bar_timestamps_are_treated_as_gap() -> None:
    completed_bars = bars(35, dt.datetime(2026, 7, 17, 14, 0))
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        dt.datetime(2026, 7, 17, 14, 40, tzinfo=UTC),
        completed_bars,
        EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_GAP
    assert_blocked_indicators(snapshot)


def test_non_breakout_when_close_equals_prior_high() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    completed_bars = custom_bars(
        BarSeriesSpec(
            35,
            start,
            closes=("100",) * 34 + ("105",),
            highs=("105",) * 35,
            lows=("99",) * 35,
        )
    )
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        completed_bars[-1].end_at + dt.timedelta(seconds=30),
        completed_bars,
        EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.READY
    assert snapshot.breakout_close_above_prior_high is False
