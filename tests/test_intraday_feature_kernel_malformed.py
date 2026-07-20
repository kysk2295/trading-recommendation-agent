from __future__ import annotations

import datetime as dt
from dataclasses import replace
from enum import StrEnum
from typing import assert_never

import pytest

from tests.intraday_feature_kernel_fixtures import (
    EXPECTED_VOLUME,
    INSTRUMENT_ID,
    UTC,
    assert_blocked_indicators,
    bars,
    identity,
)
from trading_agent.intraday_feature_kernel import (
    CompletedMinuteBar,
    FeatureSnapshotStatus,
    build_intraday_feature_snapshot,
)


class MalformedField(StrEnum):
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"


MalformedValue = str | float | int | None


def test_malformed_bar_timestamp_type_blocks_gap_without_exception() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    completed_bars = list(bars(35, start))
    completed_bars[0] = replace(
        completed_bars[0],
        start_at="2026-07-17T14:00:00+00:00",
    )
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        completed_bars[-1].end_at + dt.timedelta(seconds=30),
        tuple(completed_bars),
        EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_GAP
    assert snapshot.source_start_at is None
    assert snapshot.source_end_at == completed_bars[-1].end_at
    assert_blocked_indicators(snapshot)


def test_malformed_bar_end_timestamp_type_blocks_gap_without_exception() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    completed_bars = list(bars(35, start))
    completed_bars[-1] = replace(completed_bars[-1], end_at=1_721_234_567)
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        start + dt.timedelta(minutes=36),
        tuple(completed_bars),
        EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_GAP
    assert snapshot.source_start_at == completed_bars[0].start_at
    assert snapshot.source_end_at is None
    assert_blocked_indicators(snapshot)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        (MalformedField.OPEN, "100"),
        (MalformedField.HIGH, 101.0),
        (MalformedField.LOW, None),
        (MalformedField.CLOSE, 100),
        (MalformedField.VOLUME, "100"),
        (MalformedField.VOLUME, -1),
        (MalformedField.VOLUME, True),
    ),
)
def test_malformed_ohlc_or_volume_blocks_gap_without_exception(
    field_name: MalformedField,
    field_value: MalformedValue,
) -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
    completed_bars = list(bars(35, start))
    completed_bars[5] = _replace_field(completed_bars[5], field_name, field_value)
    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        completed_bars[-1].end_at + dt.timedelta(seconds=30),
        tuple(completed_bars),
        EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_GAP
    assert snapshot.source_start_at == completed_bars[0].start_at
    assert snapshot.source_end_at == completed_bars[-1].end_at
    assert snapshot.bar_count == 35
    assert_blocked_indicators(snapshot)


def _replace_field(
    bar: CompletedMinuteBar,
    field_name: MalformedField,
    value: MalformedValue,
) -> CompletedMinuteBar:
    match field_name:
        case MalformedField.OPEN:
            return replace(bar, open=value)
        case MalformedField.HIGH:
            return replace(bar, high=value)
        case MalformedField.LOW:
            return replace(bar, low=value)
        case MalformedField.CLOSE:
            return replace(bar, close=value)
        case MalformedField.VOLUME:
            return replace(bar, volume=value)
        case unreachable:
            assert_never(unreachable)
