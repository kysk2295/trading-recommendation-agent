from __future__ import annotations

import datetime as dt

from tests.intraday_feature_kernel_fixtures import (
    EXPECTED_VOLUME,
    INSTRUMENT_ID,
    BarSeriesSpec,
    bars,
    custom_bars,
    identity,
)
from trading_agent.intraday_feature_kernel import (
    FeatureSnapshotStatus,
    build_intraday_feature_snapshot,
)


def test_ready_snapshot_preserves_latest_completed_close() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=dt.UTC)
    completed_bars = custom_bars(
        BarSeriesSpec(35, start, closes=("100",) * 34 + ("110",))
    )

    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        completed_bars[-1].end_at + dt.timedelta(seconds=30),
        completed_bars,
        EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.READY
    assert snapshot.close == completed_bars[-1].close


def test_blocked_snapshot_does_not_expose_latest_close() -> None:
    start = dt.datetime(2026, 7, 17, 14, 0, tzinfo=dt.UTC)
    completed_bars = bars(34, start)

    snapshot = build_intraday_feature_snapshot(
        identity(),
        INSTRUMENT_ID,
        completed_bars[-1].end_at + dt.timedelta(seconds=30),
        completed_bars,
        EXPECTED_VOLUME,
    )

    assert snapshot.status is FeatureSnapshotStatus.BLOCKED_INSUFFICIENT_HISTORY
    assert snapshot.close is None
