from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent import engine as engine_module
from trading_agent.engine import RecommendationEngine
from trading_agent.models import BarInput, RecommendationState
from trading_agent.risk import RiskConfig
from trading_agent.scanner import MomentumScanner, ScannerConfig
from trading_agent.store import PaperStore
from trading_agent.strategy import OpeningRangeBreakout, OrbConfig


def test_bar_checkpoint_round_trips_latest_processed_minute(tmp_path: Path) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    timestamp = dt.datetime(
        2026, 7, 10, 10, 15, tzinfo=ZoneInfo("America/New_York")
    )

    assert store.last_processed_bar("AAA") is None

    store.set_last_processed_bar("AAA", timestamp, 10.25)

    assert store.last_processed_bar("AAA") == timestamp
    assert store.last_processed_close("AAA") == 10.25


def test_bar_checkpoint_never_moves_backward(tmp_path: Path) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    latest = dt.datetime(
        2026, 7, 10, 10, 15, tzinfo=ZoneInfo("America/New_York")
    )
    earlier = latest - dt.timedelta(minutes=1)
    store.set_last_processed_bar("AAA", latest, 106.0)

    store.set_last_processed_bar("AAA", earlier, 99.0)

    assert store.last_processed_bar("AAA") == latest
    assert store.last_processed_close("AAA") == 106.0


def test_existing_checkpoint_schema_adds_last_close_without_data_loss(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    timestamp = dt.datetime(
        2026, 7, 10, 10, 15, tzinfo=ZoneInfo("America/New_York")
    )
    with sqlite3.connect(database) as connection:
        _ = connection.execute(
            "CREATE TABLE bar_checkpoints ("
            "symbol TEXT PRIMARY KEY, processed_at TEXT NOT NULL)"
        )
        _ = connection.execute(
            "INSERT INTO bar_checkpoints VALUES (?, ?)",
            ("AAA", timestamp.isoformat()),
        )

    store = PaperStore(database)

    assert store.last_processed_bar("AAA") == timestamp
    assert store.last_processed_close("AAA") is None


def test_advance_updates_an_existing_plan_without_emitting_a_new_one(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    first_engine = _engine(store)
    opening = (_bar(30, 105.0), _bar(31, 105.5), _bar(32, 106.0))
    recommendation = first_engine.process_snapshot(opening)
    assert recommendation is not None
    target_bar = _bar(33, recommendation.target_2r)

    restarted_engine = _engine(store)
    for bar in opening:
        restarted_engine.warmup(bar)
    restarted_engine.advance(target_bar)

    persisted = store.recommendations()
    assert len(persisted) == 1
    assert persisted[0].state is RecommendationState.TARGET_2R


def test_forward_snapshot_processes_the_same_completed_bar_once(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    snapshot = (_bar(30, 105.0), _bar(31, 105.5), _bar(32, 106.0))
    first = _engine(store).process_forward(snapshot)
    assert first is not None
    event_count = len(store.events(first.recommendation_id))

    repeated = _engine(store).process_forward(snapshot)

    assert repeated is None
    assert len(store.recommendations()) == 1
    assert len(store.events(first.recommendation_id)) == event_count
    assert store.last_processed_bar("AAA") == snapshot[-1].timestamp


def test_forward_snapshot_emits_at_most_one_orb_plan_per_day(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    initial = (_bar(30, 105.0), _bar(31, 105.5), _bar(32, 106.0))
    first = _engine(store).process_forward(initial)
    assert first is not None
    extended = (*initial, _bar(33, 106.2))

    second = _engine(store).process_forward(extended)

    assert second is None
    assert len(store.recommendations()) == 1


def test_due_recommendation_time_exits_at_the_official_close(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    snapshot = (_bar(30, 105.0), _bar(31, 105.5), _bar(32, 106.0))
    recommendation = _engine(store).process_forward(snapshot)
    assert recommendation is not None
    official_close = dt.datetime(
        2026, 7, 10, 16, 0, tzinfo=ZoneInfo("America/New_York")
    )

    finalized = engine_module.finalize_due_recommendations(store, official_close)

    persisted = store.recommendations()[0]
    event = store.events(recommendation.recommendation_id)[-1]
    assert finalized == 1
    assert persisted.state is RecommendationState.TIME_EXIT
    assert event.occurred_at == official_close
    assert event.price == snapshot[-1].close
    assert "마지막 완료 봉" in event.note


def _engine(store: PaperStore) -> RecommendationEngine:
    return RecommendationEngine(
        MomentumScanner(ScannerConfig(min_gap_pct=0.04, min_relative_volume=1.0)),
        OpeningRangeBreakout(OrbConfig(range_minutes=2, volume_multiplier=1.0)),
        RiskConfig(),
        store,
    )


def _bar(minute: int, price: float) -> BarInput:
    return BarInput(
        symbol="AAA",
        timestamp=dt.datetime(
            2026, 7, 10, 9, minute, tzinfo=ZoneInfo("America/New_York")
        ),
        open=price - 0.1,
        high=price + 0.1,
        low=price - 0.2,
        close=price,
        volume=30_000,
        prior_close=100.0,
        average_daily_volume=200_000,
        spread_bps=40.0,
        catalyst="KIS 현재 랭킹",
    )
