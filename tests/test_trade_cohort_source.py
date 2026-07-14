from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent.bar_archive import CandidateInputSnapshot, archive_candidate_input
from trading_agent.market_risk import MARKET_RISK_HEADER
from trading_agent.metrics import extract_paper_trades
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.opening_gap import SNAPSHOT_HEADER
from trading_agent.store import PaperStore
from trading_agent.trade_cohort_models import FeatureStatus, TradeFeatureSource
from trading_agent.trade_cohort_source import load_trade_feature_assignments

NEW_YORK = ZoneInfo("America/New_York")


def test_trade_features_use_latest_inputs_known_when_recommendation_was_created(tmp_path: Path) -> None:
    # Given: one trade with causal and future risk/gap observations around its decision time.
    session = tmp_path / "session"
    store, created_at = _completed_trade(session)
    _candidate_input(store.path, created_at)
    risk = session / "market_risk_screen.csv"
    gap = session / "kis_opening_gap_snapshots.csv"
    _write_risk_rows(risk, created_at)
    _write_gap_rows(gap, created_at)

    # When: point-in-time cohort features are joined to the completed trade.
    assignments = load_trade_feature_assignments(
        TradeFeatureSource(store.path, risk, gap),
        extract_paper_trades((store,)),
    )

    # Then: future rows are ignored and coarse pre-registered buckets are assigned.
    assert len(assignments) == 1
    row = assignments[0]
    assert row.status is FeatureStatus.COMPLETE
    assert row.risk_observed_at == created_at - dt.timedelta(minutes=1)
    assert row.price == 8.0
    assert row.opening_gap_pct == 0.12
    assert row.price_bucket == "price_5_20"
    assert row.gap_bucket == "gap_10_20pct"
    assert row.volume_to_adv_bucket == "volume_to_adv_25_50pct"
    assert row.dollar_volume_bucket == "dollar_volume_1_5m"


def test_trade_features_censor_future_only_risk_context(tmp_path: Path) -> None:
    # Given: a trade and exact candidate input but only a risk row written after the recommendation.
    session = tmp_path / "session"
    store, created_at = _completed_trade(session)
    _candidate_input(store.path, created_at)
    risk = session / "market_risk_screen.csv"
    _write_single_risk_row(risk, created_at + dt.timedelta(minutes=1), 99.0)

    # When: cohort features are loaded.
    assignments = load_trade_feature_assignments(
        TradeFeatureSource(store.path, risk, None),
        extract_paper_trades((store,)),
    )

    # Then: the future context is not backfilled into the trade.
    assert assignments[0].status is FeatureStatus.CENSORED
    assert assignments[0].reason == "point_in_time_risk_missing"
    assert assignments[0].price is None


def _completed_trade(session: Path) -> tuple[PaperStore, dt.datetime]:
    store = PaperStore(session / "paper_recommendations.sqlite3")
    created_at = dt.datetime(2026, 7, 14, 10, 0, tzinfo=NEW_YORK)
    recommendation = Recommendation(
        "cohort-one",
        "DEMO",
        "opening_range_breakout",
        created_at,
        8.0,
        7.5,
        8.5,
        9.0,
        RecommendationState.SETUP,
        "fixture",
    )
    store.save(recommendation)
    store.set_state(
        recommendation.recommendation_id,
        RecommendationState.ACTIVE,
        created_at + dt.timedelta(minutes=1),
        8.0,
        "조건부 진입가 도달",
    )
    store.set_state(
        recommendation.recommendation_id,
        RecommendationState.TARGET_2R,
        created_at + dt.timedelta(minutes=5),
        9.0,
        "2R 목표가 도달",
    )
    return store, created_at


def _candidate_input(database: Path, created_at: dt.datetime) -> None:
    _ = archive_candidate_input(
        database,
        CandidateInputSnapshot(
            "NAS",
            "DEMO",
            created_at,
            created_at - dt.timedelta(minutes=1),
            7.0,
            1_000_000,
            25.0,
        ),
    )


def _write_risk_rows(path: Path, created_at: dt.datetime) -> None:
    _write_single_risk_row(path, created_at - dt.timedelta(minutes=1), 8.0)
    with path.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(_risk_row(created_at + dt.timedelta(minutes=1), 99.0))


def _write_single_risk_row(path: Path, observed_at: dt.datetime, price: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MARKET_RISK_HEADER)
        writer.writerow(_risk_row(observed_at, price))


def _risk_row(observed_at: dt.datetime, price: float) -> tuple[str | float | int | bool, ...]:
    return (
        observed_at.isoformat(),
        "NAS",
        "DEMO",
        True,
        "",
        0.14,
        price,
        price - 0.01,
        price + 0.01,
        25.0,
        65.0,
        2_000_000.0,
        300_000,
        1_000_000,
        0.30,
    )


def _write_gap_rows(path: Path, created_at: dt.datetime) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SNAPSHOT_HEADER)
        writer.writerow(_gap_row(created_at - dt.timedelta(minutes=2), 0.12))
        writer.writerow(_gap_row(created_at + dt.timedelta(minutes=1), 0.90))


def _gap_row(observed_at: dt.datetime, gap: float) -> tuple[str | float | int, ...]:
    return (
        observed_at.isoformat(),
        (observed_at + dt.timedelta(seconds=10)).isoformat(),
        "NAS",
        "DEMO",
        "ok",
        7.0,
        7.84,
        gap,
        8.0,
        300_000,
        1_000_000,
        "",
    )
