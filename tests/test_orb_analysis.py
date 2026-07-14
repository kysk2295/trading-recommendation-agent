from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from scr_backtest.kis_intraday import KisMinuteBar
from trading_agent import orb_analysis
from trading_agent.bar_archive import CandidateBarBatch, archive_candidate_bars
from trading_agent.kis_provider import KisRankedStock
from trading_agent.orb_models import OrbOutcome, OrbOutcomeStatus, OrbTestConfig
from trading_agent.ranking_journal import (
    RankingGroup,
    RankingSnapshot,
    RankingSource,
    append_ranking_snapshot,
)


def test_orb_analysis_reads_exact_selection_and_archived_bars(
    tmp_path: Path,
) -> None:
    new_york = ZoneInfo("America/New_York")
    seoul = ZoneInfo("Asia/Seoul")
    observed_at = dt.datetime(2026, 7, 10, 9, 36, 30, tzinfo=new_york)
    stock = KisRankedStock(
        "NAS",
        "GRID",
        "Grid Corp",
        10.1,
        0.1,
        10.09,
        10.11,
        500_000,
        5_000_000.0,
        200_000,
        1,
    )
    snapshot = tmp_path / "kis_ranking_snapshots.csv"
    append_ranking_snapshot(
        snapshot,
        RankingSnapshot(
            observed_at,
            (RankingGroup(RankingSource.VOLUME, "NAS", (stock,)),),
            (stock,),
        ),
    )
    start = dt.datetime(2026, 7, 10, 9, 30, tzinfo=new_york)
    bars = tuple(
        KisMinuteBar(
            timestamp,
            timestamp.astimezone(seoul),
            9.9,
            10.2 if offset >= 5 else 10.0,
            9.8 if offset < 5 else 9.9,
            10.1 if offset == 5 else 9.9,
            200 if offset == 5 else 100,
            2_000,
        )
        for offset in range(390)
        for timestamp in (start + dt.timedelta(minutes=offset),)
    )
    database = tmp_path / "paper.sqlite3"
    _ = archive_candidate_bars(
        database,
        CandidateBarBatch("NAS", "GRID", observed_at, bars),
    )
    config = OrbTestConfig(5, 5.0, 1.5, 1.0, 2.0)

    outcomes = orb_analysis.analyze_orb_grid(snapshot, database, (config,))

    assert len(outcomes) == 1
    assert outcomes[0].symbol == "GRID"
    assert outcomes[0].status is OrbOutcomeStatus.TIME_EXIT
    assert outcomes[0].portfolio_selected


def test_orb_portfolio_capacity_selects_the_strongest_ten() -> None:
    observed_at = dt.datetime(
        2026,
        7,
        10,
        9,
        36,
        30,
        tzinfo=ZoneInfo("America/New_York"),
    )
    config = OrbTestConfig(5, 5.0, 1.5, 1.0, 2.0)
    base = OrbOutcome(
        config,
        observed_at,
        "NAS",
        "BASE",
        0.1,
        5_000_000.0,
        20.0,
        True,
        OrbOutcomeStatus.TIME_EXIT,
        observed_at,
        observed_at + dt.timedelta(minutes=1),
        observed_at + dt.timedelta(minutes=10),
        10.0,
        9.5,
        11.0,
        10.5,
        0.05,
    )
    candidates = tuple(
        dataclasses.replace(
            base,
            symbol=f"S{index:02d}",
            change_pct=index / 100.0,
        )
        for index in range(11)
    )

    selected = orb_analysis.apply_portfolio_limit(candidates, max_positions=10)

    kept = tuple(row.symbol for row in selected if row.portfolio_selected)
    assert len(kept) == 10
    assert "S00" not in kept
