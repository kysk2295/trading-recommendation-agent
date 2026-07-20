from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from tests.us_volume_profile_fixtures import volume_profile
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.intraday_feature_kernel import CompletedMinuteBar, IntradayFeatureSnapshot
from trading_agent.research_input_identity import ResearchInputIdentity

UTC = dt.UTC
SCOPE = "us_equities.day_trading.orb"
INSTRUMENT_ID = "us-eq-fixture-aapl"
EXPECTED_VOLUME = volume_profile(
    INSTRUMENT_ID,
    dt.date(2026, 7, 17),
    expected_cumulative_volume=10_000,
)
INDICATOR_NONE_FIELDS = (
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


@dataclass(frozen=True, slots=True)
class BarSeriesSpec:
    count: int
    start: dt.datetime
    closes: tuple[str, ...] | None = None
    highs: tuple[str, ...] | None = None
    lows: tuple[str, ...] | None = None
    volumes: tuple[int, ...] | None = None


def identity() -> ResearchInputIdentity:
    replay = CanonicalDatasetReplay(
        dataset_id="ds_fixture",
        event_count=1,
        canonical_event_content_sha256="a" * 64,
        parquet_sha256="c" * 64,
        raw_manifest_id="raw_manifest_fixture",
        raw_manifest_content_sha256="b" * 64,
    )
    return ResearchInputIdentity.from_verified_replay(SCOPE, replay)


def bar(start: dt.datetime) -> CompletedMinuteBar:
    return CompletedMinuteBar(
        start_at=start,
        end_at=start + dt.timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=100,
    )


def bars(count: int, start: dt.datetime) -> tuple[CompletedMinuteBar, ...]:
    return custom_bars(BarSeriesSpec(count, start))


def custom_bars(spec: BarSeriesSpec) -> tuple[CompletedMinuteBar, ...]:
    result: list[CompletedMinuteBar] = []
    for index in range(spec.count):
        close = (
            spec.closes[index]
            if spec.closes is not None
            else str(Decimal("100") + (Decimal(index % 5) * Decimal("0.1")))
        )
        close_dec = Decimal(close)
        high_dec = (
            Decimal(spec.highs[index])
            if spec.highs is not None
            else close_dec + Decimal("0.5")
        )
        low_dec = (
            Decimal(spec.lows[index])
            if spec.lows is not None
            else close_dec - Decimal("0.5")
        )
        volume = spec.volumes[index] if spec.volumes is not None else 100 + index
        result.append(
            CompletedMinuteBar(
                start_at=spec.start + dt.timedelta(minutes=index),
                end_at=spec.start + dt.timedelta(minutes=index + 1),
                open=close_dec,
                high=high_dec,
                low=low_dec,
                close=close_dec,
                volume=volume,
            )
        )
    return tuple(result)


def assert_blocked_indicators(snapshot: IntradayFeatureSnapshot) -> None:
    for field_name in INDICATOR_NONE_FIELDS:
        assert getattr(snapshot, field_name) is None


__all__ = (
    "EXPECTED_VOLUME",
    "INSTRUMENT_ID",
    "UTC",
    "BarSeriesSpec",
    "assert_blocked_indicators",
    "bar",
    "bars",
    "custom_bars",
    "identity",
)
