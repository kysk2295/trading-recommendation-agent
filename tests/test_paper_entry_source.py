from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scr_backtest.kis_intraday import KisMinuteBar
from trading_agent.bar_archive import (
    CandidateBarBatch,
    CandidateInputSnapshot,
    archive_candidate_bars,
    archive_candidate_input,
)
from trading_agent.lane_defaults import INTRADAY_PILOT_PAPER_RISK_CONFIG
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.paper_entry_source import (
    InvalidCurrentOrbPaperEntrySourceError,
    _connect_readonly,
    load_current_orb_paper_entry,
)
from trading_agent.paper_execution_models import PaperOrderSide
from trading_agent.store import PaperStore

NEW_YORK = ZoneInfo("America/New_York")
SEOUL = ZoneInfo("Asia/Seoul")
BAR_START = dt.datetime(2026, 7, 14, 9, 35, tzinfo=NEW_YORK)
OBSERVED_AT = dt.datetime(2026, 7, 14, 9, 36, 2, tzinfo=NEW_YORK)
EVALUATED_AT = dt.datetime(2026, 7, 14, 9, 36, 4, tzinfo=NEW_YORK)
STRATEGY = "opening_range_breakout"
RECOMMENDATION_ID = f"{OBSERVED_AT.isoformat()}:AAPL:{STRATEGY}"


def _write_valid_source(path: Path, symbol: str = "AAPL") -> str:
    recommendation_id = f"{OBSERVED_AT.isoformat()}:{symbol}:{STRATEGY}"
    store = PaperStore(path)
    store.save(
        Recommendation(
            recommendation_id,
            symbol,
            STRATEGY,
            OBSERVED_AT,
            10.0,
            9.75,
            10.25,
            10.50,
            RecommendationState.SETUP,
            "current ORB fixture",
        )
    )
    _ = archive_candidate_input(
        path,
        CandidateInputSnapshot(
            "NAS",
            symbol,
            OBSERVED_AT.astimezone(SEOUL),
            BAR_START,
            8.0,
            1_000_000,
            12.5,
        ),
    )
    _ = archive_candidate_bars(
        path,
        CandidateBarBatch(
            "NAS",
            symbol,
            OBSERVED_AT.astimezone(dt.UTC),
            (
                KisMinuteBar(
                    BAR_START,
                    BAR_START.astimezone(SEOUL),
                    9.80,
                    10.10,
                    9.75,
                    10.05,
                    10_000,
                    100_000,
                ),
            ),
        ),
    )
    return recommendation_id


def test_loads_one_exact_current_orb_candidate_across_timezone_offsets(
    tmp_path: Path,
) -> None:
    database = tmp_path / "paper_recommendations.sqlite3"
    _write_valid_source(database)

    request = load_current_orb_paper_entry(database, EVALUATED_AT)

    intent = request.candidate_intent
    assert intent.intent_id == RECOMMENDATION_ID
    assert intent.strategy_id == "orb"
    assert intent.strategy_version == "paper-smoke-v1"
    assert intent.symbol == "AAPL"
    assert intent.created_at == OBSERVED_AT
    assert intent.side is PaperOrderSide.BUY
    assert intent.entry_limit == 10.0
    assert intent.stop == 9.75
    assert intent.target_1r == 10.25
    assert intent.target_2r == 10.50
    assert request.latest_bar.symbol == "AAPL"
    assert request.latest_bar.started_at == BAR_START
    assert request.latest_bar.first_observed_at == OBSERVED_AT
    assert request.liquidity_allowed_quantity == 1
    assert request.estimated_spread_bps == 12.5
    assert request.config is INTRADAY_PILOT_PAPER_RISK_CONFIG


def test_missing_database_is_rejected_without_creation(tmp_path: Path) -> None:
    database = tmp_path / "missing/paper_recommendations.sqlite3"

    with pytest.raises(InvalidCurrentOrbPaperEntrySourceError) as captured:
        _ = load_current_orb_paper_entry(database, EVALUATED_AT)

    assert str(captured.value) == "현재 ORB Paper entry source를 안전하게 확정하지 못했습니다"
    assert str(database) not in str(captured.value)
    assert not database.exists()
    assert not database.parent.exists()


@pytest.mark.parametrize(
    "evaluated_at",
    (
        OBSERVED_AT - dt.timedelta(microseconds=1),
        OBSERVED_AT + dt.timedelta(seconds=31),
    ),
)
def test_rejects_future_or_stale_recommendation(
    tmp_path: Path,
    evaluated_at: dt.datetime,
) -> None:
    database = tmp_path / "paper_recommendations.sqlite3"
    _write_valid_source(database)

    with pytest.raises(InvalidCurrentOrbPaperEntrySourceError):
        _ = load_current_orb_paper_entry(database, evaluated_at)


@pytest.mark.parametrize(
    ("latest_completed", "first_observed"),
    (
        (BAR_START - dt.timedelta(minutes=1), OBSERVED_AT),
        (BAR_START, BAR_START + dt.timedelta(seconds=59)),
    ),
)
def test_rejects_wrong_minute_or_unfinished_bar(
    tmp_path: Path,
    latest_completed: dt.datetime,
    first_observed: dt.datetime,
) -> None:
    database = tmp_path / "paper_recommendations.sqlite3"
    _write_valid_source(database)
    with sqlite3.connect(database) as connection:
        _ = connection.execute(
            "UPDATE candidate_input_snapshots SET latest_completed_bar_at = ?",
            (latest_completed.isoformat(),),
        )
        _ = connection.execute(
            "UPDATE candidate_minute_bars SET exchange_timestamp = ?, first_observed_at = ?",
            (latest_completed.isoformat(), first_observed.isoformat()),
        )

    with pytest.raises(InvalidCurrentOrbPaperEntrySourceError):
        _ = load_current_orb_paper_entry(database, EVALUATED_AT)


def test_rejects_multiple_exact_current_candidates(tmp_path: Path) -> None:
    database = tmp_path / "paper_recommendations.sqlite3"
    _write_valid_source(database, "AAPL")
    _write_valid_source(database, "MSFT")

    with pytest.raises(InvalidCurrentOrbPaperEntrySourceError):
        _ = load_current_orb_paper_entry(database, EVALUATED_AT)


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE recommendations SET recommendation_id = 'forged-id'",
        "UPDATE recommendations SET entry = -1",
        "UPDATE recommendations SET target_1r = entry",
        "UPDATE candidate_input_snapshots SET spread_bps = -1",
        "UPDATE candidate_minute_bars SET volume = 0",
    ),
)
def test_rejects_invalid_identity_prices_spread_or_volume(
    tmp_path: Path,
    statement: str,
) -> None:
    database = tmp_path / "paper_recommendations.sqlite3"
    _write_valid_source(database)
    with sqlite3.connect(database) as connection:
        _ = connection.execute(statement)

    with pytest.raises(InvalidCurrentOrbPaperEntrySourceError):
        _ = load_current_orb_paper_entry(database, EVALUATED_AT)


def test_rejects_malformed_timestamp_or_missing_schema(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.sqlite3"
    _write_valid_source(malformed)
    with sqlite3.connect(malformed) as connection:
        _ = connection.execute("UPDATE candidate_input_snapshots SET observed_at = '2026-07-14T09:36:02'")
    with pytest.raises(InvalidCurrentOrbPaperEntrySourceError):
        _ = load_current_orb_paper_entry(malformed, EVALUATED_AT)

    missing_schema = tmp_path / "missing-schema.sqlite3"
    with sqlite3.connect(missing_schema):
        pass
    with pytest.raises(InvalidCurrentOrbPaperEntrySourceError):
        _ = load_current_orb_paper_entry(missing_schema, EVALUATED_AT)


def test_source_connection_is_query_only(tmp_path: Path) -> None:
    database = tmp_path / "paper_recommendations.sqlite3"
    _write_valid_source(database)

    with closing(_connect_readonly(database)) as connection:
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError):
            _ = connection.execute("DELETE FROM recommendations")

    assert len(PaperStore(database).recommendations()) == 1
