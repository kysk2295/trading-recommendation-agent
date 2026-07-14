from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from scr_backtest.kis_intraday import KisMinuteBar
from trading_agent import kis_live
from trading_agent.engine import RecommendationEngine
from trading_agent.models import BarInput, RecommendationState
from trading_agent.risk import RiskConfig
from trading_agent.scanner import MomentumScanner, ScannerConfig
from trading_agent.store import PaperStore
from trading_agent.strategy import OpeningRangeBreakout, OrbConfig

DEFAULT_SESSION_DATE: Final = dt.date(2026, 7, 10)


def test_warmup_never_backdates_a_recommendation(tmp_path: Path) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )
    historical = (
        _bar(30, 105.0, 20_000),
        _bar(31, 105.5, 20_000),
        _bar(32, 106.0, 30_000),
    )

    for bar in historical:
        engine.warmup(bar)

    assert store.recommendations() == ()


def test_snapshot_only_evaluates_the_latest_completed_minute(tmp_path: Path) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )
    snapshot = (
        _bar(30, 105.0, 20_000),
        _bar(31, 105.5, 20_000),
        _bar(32, 106.0, 30_000),
        _bar(33, 105.0, 30_000),
    )

    recommendation = engine.process_snapshot(snapshot)

    assert recommendation is None
    assert store.recommendations() == ()


def test_snapshot_timestamps_a_current_breakout_after_the_bar_completes(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )
    snapshot = (
        _bar(30, 105.0, 20_000),
        _bar(31, 105.5, 20_000),
        _bar(32, 106.0, 30_000),
    )

    recommendation = engine.process_snapshot(snapshot)

    assert recommendation is not None
    assert recommendation.created_at == snapshot[-1].timestamp + dt.timedelta(minutes=1)
    assert recommendation.state is RecommendationState.SETUP


def test_snapshot_rejects_a_breakout_that_is_known_only_at_the_close(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )
    snapshot = (
        _bar(30, 105.0, 20_000),
        _bar(31, 105.5, 20_000),
        _bar_at(dt.time(15, 59), 106.0, 30_000),
    )

    recommendation = engine.process_snapshot(snapshot)

    assert recommendation is None
    assert store.recommendations() == ()


def test_forward_recommendation_never_predates_the_actual_observation(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )
    snapshot = (
        _bar(30, 105.0, 20_000),
        _bar(31, 105.5, 20_000),
        _bar(32, 106.0, 30_000),
    )
    observed_at = dt.datetime(
        2026,
        7,
        10,
        22,
        33,
        30,
        tzinfo=ZoneInfo("Asia/Seoul"),
    )

    recommendation = engine.process_forward(snapshot, observed_at)

    assert recommendation is not None
    assert recommendation.created_at == observed_at.astimezone(ZoneInfo("America/New_York"))
    assert recommendation.created_at.tzinfo == ZoneInfo("America/New_York")


def test_forward_recommendation_skips_bar_started_before_actual_observation(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )
    snapshot = (
        _bar(30, 105.0, 20_000),
        _bar(31, 105.5, 20_000),
        _bar(32, 106.0, 30_000),
    )
    observed_at = dt.datetime(
        2026,
        7,
        10,
        9,
        33,
        30,
        tzinfo=ZoneInfo("America/New_York"),
    )
    recommendation = engine.process_forward(snapshot, observed_at)
    assert recommendation is not None
    started_before_alert = _bar(33, recommendation.target_2r, 30_000)

    _ = engine.process_forward(
        (*snapshot, started_before_alert),
        observed_at + dt.timedelta(minutes=1),
    )

    assert store.recommendations()[0].state is RecommendationState.SETUP
    first_full_bar = _bar(34, recommendation.target_2r, 30_000)
    _ = engine.process_forward(
        (*snapshot, started_before_alert, first_full_bar),
        observed_at + dt.timedelta(minutes=2),
    )
    assert store.recommendations()[0].state is RecommendationState.TARGET_2R


def test_completed_minutes_exclude_the_still_forming_bar() -> None:
    observed_at = dt.datetime(2026, 7, 10, 10, 0, 30, tzinfo=ZoneInfo("America/New_York"))
    minutes = (_minute_bar(9, 59), _minute_bar(10, 0))

    completed = kis_live.completed_regular_minutes(minutes, observed_at)

    assert tuple(bar.exchange_timestamp.minute for bar in completed) == (59,)


def test_completed_minutes_exclude_a_previous_session() -> None:
    observed_at = dt.datetime(2026, 7, 10, 9, 32, tzinfo=ZoneInfo("America/New_York"))
    previous = _minute_bar(15, 59, session_date=dt.date(2026, 7, 9))

    completed = kis_live.completed_regular_minutes((previous,), observed_at)

    assert completed == ()


def _bar(minute: int, price: float, volume: int) -> BarInput:
    return _bar_at(dt.time(9, minute), price, volume)


def _bar_at(exchange_time: dt.time, price: float, volume: int) -> BarInput:
    return BarInput(
        symbol="AAA",
        timestamp=dt.datetime.combine(
            DEFAULT_SESSION_DATE,
            exchange_time,
            tzinfo=ZoneInfo("America/New_York"),
        ),
        open=price - 0.1,
        high=price + 0.1,
        low=price - 0.2,
        close=price,
        volume=volume,
        prior_close=100.0,
        average_daily_volume=200_000,
        spread_bps=40.0,
        catalyst="KIS 현재 랭킹",
    )


def _minute_bar(
    hour: int,
    minute: int,
    session_date: dt.date = DEFAULT_SESSION_DATE,
) -> KisMinuteBar:
    new_york = ZoneInfo("America/New_York")
    seoul = ZoneInfo("Asia/Seoul")
    timestamp = dt.datetime.combine(
        session_date,
        dt.time(hour, minute),
        tzinfo=new_york,
    )
    return KisMinuteBar(
        exchange_timestamp=timestamp,
        korea_timestamp=timestamp.astimezone(seoul),
        open=10.0,
        high=10.1,
        low=9.9,
        close=10.0,
        volume=100,
        amount=1_000,
    )
