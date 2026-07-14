from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading_agent import replay
from trading_agent.engine import RecommendationEngine
from trading_agent.models import BarInput, RecommendationState, StrategySignal
from trading_agent.risk import RiskConfig, build_trade_plan
from trading_agent.scanner import MomentumScanner, ScannerConfig
from trading_agent.store import PaperStore
from trading_agent.strategy import OpeningRangeBreakout, OrbConfig


def test_scanner_only_accepts_live_momentum_candidates() -> None:
    scanner = MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=2.0))

    rejected = scanner.observe(_bar("AAA", 9, 30, 103.0, 100_000, 100.0, 1_000_000))
    accepted = scanner.observe(_bar("BBB", 9, 30, 106.0, 20_000, 100.0, 100_000))

    assert rejected is None
    assert accepted is not None
    assert accepted.symbol == "BBB"
    assert accepted.gap_pct == pytest.approx(0.059)


def test_scanner_keeps_regular_session_open_for_gap() -> None:
    scanner = MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0))
    opening = _bar("AAA", 9, 30, 105.0, 20_000, 100.0, 200_000)
    later = _bar("AAA", 9, 35, 106.0, 30_000, 100.0, 200_000)
    later = BarInput(
        later.symbol,
        later.timestamp,
        99.0,
        later.high,
        later.low,
        later.close,
        later.volume,
        later.prior_close,
        later.average_daily_volume,
        later.spread_bps,
        later.catalyst,
    )
    _ = scanner.observe(opening)

    candidate = scanner.observe(later)

    assert candidate is not None
    assert candidate.gap_pct == pytest.approx(0.049)


def test_orb_emits_after_range_break_with_volume_confirmation() -> None:
    strategy = OpeningRangeBreakout(OrbConfig(range_minutes=5, breakout_buffer_bps=5.0, volume_multiplier=1.2))
    scanner = MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0))
    signals: list[StrategySignal] = []
    for minute, price, volume in (
        (30, 105.0, 10_000),
        (31, 105.5, 10_000),
        (32, 105.2, 10_000),
        (33, 105.8, 10_000),
        (34, 105.4, 10_000),
        (35, 106.2, 20_000),
    ):
        bar = _bar("AAA", 9, minute, price, volume, 100.0, 200_000)
        candidate = scanner.observe(bar)
        signal = strategy.observe(bar, candidate)
        if signal is not None:
            signals.append(signal)

    assert len(signals) == 1
    assert signals[0].entry > 105.8
    assert signals[0].stop < signals[0].entry


def test_orb_ignores_premarket_bars_and_uses_new_york_open() -> None:
    strategy = OpeningRangeBreakout(OrbConfig(range_minutes=2, breakout_buffer_bps=0.0, volume_multiplier=1.0))
    scanner = MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0))

    for minute in (20, 21):
        premarket = _bar("AAA", 9, minute, 110.0, 50_000, 100.0, 200_000)
        assert strategy.observe(premarket, scanner.observe(premarket)) is None

    opening_1 = _bar("AAA", 9, 30, 105.0, 20_000, 100.0, 200_000)
    opening_2 = _bar("AAA", 9, 31, 105.5, 20_000, 100.0, 200_000)
    breakout = _bar("AAA", 9, 32, 106.0, 30_000, 100.0, 200_000)
    assert strategy.observe(opening_1, scanner.observe(opening_1)) is None
    assert strategy.observe(opening_2, scanner.observe(opening_2)) is None

    signal = strategy.observe(breakout, scanner.observe(breakout))

    assert signal is not None
    assert signal.entry < 110.0


def test_risk_plan_rejects_excessive_spread_and_calculates_targets() -> None:
    plan = build_trade_plan(20.0, 19.5, 40.0, RiskConfig())

    assert plan is not None
    assert plan.target_1r == 20.5
    assert plan.target_2r == 21.0
    assert build_trade_plan(20.0, 19.5, 120.0, RiskConfig()) is None


def test_engine_records_setup_activation_and_target(tmp_path: Path) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )
    for minute, price in ((30, 105.0), (31, 105.5), (32, 106.0)):
        _ = engine.process(_bar("AAA", 9, minute, price, 20_000, 100.0, 200_000))
    setup = store.recommendations()[0]
    _ = engine.process(
        _bar(
            "AAA",
            9,
            33,
            setup.target_2r + 0.1,
            30_000,
            100.0,
            200_000,
        )
    )

    final = store.recommendations()[0]
    states = tuple(event.state for event in store.events(final.recommendation_id))
    assert final.state is RecommendationState.TARGET_2R
    assert states == (
        RecommendationState.SETUP,
        RecommendationState.ACTIVE,
        RecommendationState.TARGET_2R,
    )


def test_engine_uses_bar_open_when_price_gaps_above_conditional_entry(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )
    for minute, price in ((30, 105.0), (31, 105.5), (32, 106.0)):
        _ = engine.process(
            _bar("AAA", 9, minute, price, 20_000, 100.0, 200_000)
        )
    setup = store.recommendations()[0]
    open_price = setup.entry + 0.05
    gap_through = BarInput(
        "AAA",
        dt.datetime(2026, 1, 2, 9, 33, tzinfo=ZoneInfo("America/New_York")),
        open_price,
        open_price + 0.05,
        open_price - 0.02,
        open_price + 0.02,
        30_000,
        100.0,
        200_000,
        40.0,
        "earnings",
    )

    _ = engine.process(gap_through)

    active = next(
        event
        for event in store.events(setup.recommendation_id)
        if event.state is RecommendationState.ACTIVE
    )
    assert active.price == open_price


def test_engine_uses_bar_open_when_price_gaps_beyond_target(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )
    for minute, price in ((30, 105.0), (31, 105.5), (32, 106.0)):
        _ = engine.process(
            _bar("AAA", 9, minute, price, 20_000, 100.0, 200_000)
        )
    setup = store.recommendations()[0]
    open_price = setup.target_2r + 0.05
    gap_through = BarInput(
        "AAA",
        dt.datetime(2026, 1, 2, 9, 33, tzinfo=ZoneInfo("America/New_York")),
        open_price,
        open_price + 0.05,
        open_price - 0.02,
        open_price + 0.02,
        30_000,
        100.0,
        200_000,
        40.0,
        "earnings",
    )

    _ = engine.process(gap_through)

    target = next(
        event
        for event in store.events(setup.recommendation_id)
        if event.state is RecommendationState.TARGET_2R
    )
    assert target.price == open_price


def test_same_bar_stop_and_target_uses_conservative_stop(tmp_path: Path) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    engine = RecommendationEngine(
        MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )
    for minute, price in ((30, 105.0), (31, 105.5), (32, 106.0)):
        _ = engine.process(_bar("AAA", 9, minute, price, 20_000, 100.0, 200_000))
    setup = store.recommendations()[0]
    collision = _bar("AAA", 9, 33, setup.entry, 30_000, 100.0, 200_000)
    collision = BarInput(
        collision.symbol,
        collision.timestamp,
        collision.open,
        setup.target_2r + 0.1,
        setup.stop - 0.1,
        collision.close,
        collision.volume,
        collision.prior_close,
        collision.average_daily_volume,
        collision.spread_bps,
        collision.catalyst,
    )

    _ = engine.process(collision)

    assert store.recommendations()[0].state is RecommendationState.STOPPED


def test_load_bars_uses_a_typed_error_for_a_timestamp_without_offset(
    tmp_path: Path,
) -> None:
    source = tmp_path / "naive.csv"
    _ = source.write_text("timestamp\n2026-07-13T09:30:00\n", encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        _ = replay.load_bars(source)

    assert caught.type.__name__ == "InvalidBarTimestampError"


def _bar(
    symbol: str,
    hour: int,
    minute: int,
    price: float,
    volume: int,
    prior_close: float,
    average_daily_volume: int,
) -> BarInput:
    return BarInput(
        symbol=symbol,
        timestamp=dt.datetime(2026, 1, 2, hour, minute, tzinfo=ZoneInfo("America/New_York")),
        open=price - 0.1,
        high=price + 0.1,
        low=price - 0.2,
        close=price,
        volume=volume,
        prior_close=prior_close,
        average_daily_volume=average_daily_volume,
        spread_bps=40.0,
        catalyst="earnings",
    )
