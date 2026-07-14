from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scr_backtest.kis_intraday import KisMinuteBar
from trading_agent import forward_outcomes
from trading_agent.bar_archive import CandidateBarBatch, archive_candidate_bars
from trading_agent.kis_provider import KisRankedStock
from trading_agent.ranking_journal import (
    RankingGroup,
    RankingSnapshot,
    RankingSource,
    append_ranking_snapshot,
)


def test_completed_session_uses_next_minute_open_and_measures_path(
    tmp_path: Path,
) -> None:
    new_york = ZoneInfo("America/New_York")
    seoul = ZoneInfo("Asia/Seoul")
    observed_at = dt.datetime(2026, 7, 10, 9, 35, 30, tzinfo=new_york)
    stock = KisRankedStock(
        "NAS",
        "PATH",
        "Path Corp",
        10.0,
        0.1,
        9.99,
        10.01,
        500_000,
        5_000_000.0,
        200_000,
        1,
    )
    snapshots = tmp_path / "kis_ranking_snapshots.csv"
    append_ranking_snapshot(
        snapshots,
        RankingSnapshot(
            observed_at,
            (RankingGroup(RankingSource.VOLUME, "NAS", (stock,)),),
            (stock,),
        ),
    )
    append_ranking_snapshot(
        snapshots,
        RankingSnapshot(
            observed_at + dt.timedelta(minutes=1),
            (RankingGroup(RankingSource.VOLUME, "NAS", (stock,)),),
            (stock,),
        ),
    )
    first_bar = dt.datetime(2026, 7, 10, 9, 36, tzinfo=new_york)
    bars = tuple(
        KisMinuteBar(
            exchange_timestamp=timestamp,
            korea_timestamp=timestamp.astimezone(seoul),
            open=10.0,
            high=12.0,
            low=9.0,
            close=11.0 if timestamp.time() == dt.time(15, 59) else 10.0,
            volume=1_000,
            amount=10_000,
        )
        for timestamp in (
            first_bar + dt.timedelta(minutes=offset) for offset in range(384)
        )
    )
    database = tmp_path / "paper.sqlite3"
    _ = archive_candidate_bars(
        database,
        CandidateBarBatch("NAS", "PATH", observed_at, bars),
    )

    outcomes = forward_outcomes.analyze_forward_outcomes(snapshots, database)

    assert len(outcomes) == 1
    result = outcomes[0]
    assert result.complete
    assert result.entry_at == first_bar
    assert result.entry == 10.0
    assert result.bar_count == 384
    assert result.return_5m == 0.0
    assert result.return_15m == 0.0
    assert result.return_30m == 0.0
    assert result.eod_return == pytest.approx(0.1)
    assert result.mfe == pytest.approx(0.2)
    assert result.mae == pytest.approx(-0.1)


def test_late_complete_session_leaves_unavailable_horizons_empty() -> None:
    new_york = ZoneInfo("America/New_York")
    observed_at = dt.datetime(2026, 7, 10, 15, 55, 30, tzinfo=new_york)
    snapshot = forward_outcomes.SelectedSnapshot(
        observed_at,
        "NAS",
        "LATE",
        10.0,
        0.1,
        20.0,
        5_000_000.0,
    )
    bars = tuple(
        forward_outcomes.ArchivedBar(
            "NAS",
            "LATE",
            observed_at.replace(minute=56, second=0)
            + dt.timedelta(minutes=offset),
            observed_at + dt.timedelta(minutes=offset + 1),
            10.0,
            10.2,
            9.9,
            10.1,
        )
        for offset in range(4)
    )

    result = forward_outcomes._measure(snapshot, bars)

    assert result.complete
    assert result.return_5m is None
    assert result.return_15m is None
    assert result.return_30m is None
    assert result.eod_return == pytest.approx(0.01)
