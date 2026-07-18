from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import run_kis_paper_scan
import run_kis_paper_watch
from trading_agent.kis_provider import KisRankedStock
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.ranking_journal import RankingGroup, RankingSource
from trading_agent.store import PaperStore


def test_watch_scan_command_uses_ten_candidate_portfolio_by_default(tmp_path: Path) -> None:
    # Given: one-minute repeated watch collection needs only the latest API page.
    max_pages = 1

    # When: the watch builds its child scan command.
    command = run_kis_paper_watch._scan_command(
        tmp_path,
        run_kis_paper_watch.WatchScanConfig(
            run_kis_paper_watch.StrategyMode.GAP_AND_GO,
            10,
            max_pages,
        ),
    )

    # Then: both the portfolio cap and bounded history request reach the child.
    assert command[-4:] == ("--top", "10", "--max-pages", "1")


def test_premarket_scan_command_is_rankings_only(tmp_path: Path) -> None:
    # Given: a shared session output and the maximum candidate portfolio.
    top = 10

    # When: the watch builds the premarket child command.
    command = run_kis_paper_watch._premarket_scan_command(tmp_path, top)

    # Then: it uses the dedicated snapshot CLI without a strategy argument.
    assert command[-4:] == ("--output-dir", str(tmp_path), "--top", "10")
    assert "run_kis_premarket_scan.py" in command[0]


def test_premarket_collection_stops_when_regular_session_opens() -> None:
    # Given: clocks spanning premarket start, one five-minute cycle, and open.
    new_york = ZoneInfo("America/New_York")
    times = iter(
        (
            dt.datetime(2026, 7, 13, 3, 59, tzinfo=new_york),
            dt.datetime(2026, 7, 13, 4, 0, tzinfo=new_york),
            dt.datetime(2026, 7, 13, 4, 5, tzinfo=new_york),
            dt.datetime(2026, 7, 13, 9, 30, tzinfo=new_york),
        )
    )
    waits: list[float] = []
    outcomes = iter((0, 1))

    # When: the collector waits, samples every five minutes, and reaches open.
    result = run_kis_paper_watch.collect_premarket_until_regular_open(
        lambda: next(times),
        waits.append,
        lambda: next(outcomes),
        run_kis_paper_watch.PremarketWaitConfig(
            max_wait=dt.timedelta(hours=8),
            closed_poll_seconds=30.0,
            collection_interval_seconds=300.0,
        ),
    )

    # Then: only premarket cycles ran and regular-open time is returned.
    assert result.opened_at == dt.datetime(2026, 7, 13, 9, 30, tzinfo=new_york)
    assert result.exit_codes == (0, 1)
    assert waits == [30.0, 300.0, 300.0]


def test_watch_finalizes_open_recommendations_after_the_session_close(
    tmp_path: Path,
) -> None:
    database = tmp_path / "paper_recommendations.sqlite3"
    store = PaperStore(database)
    created_at = dt.datetime(2026, 7, 10, 15, 50, tzinfo=ZoneInfo("America/New_York"))
    store.save(
        Recommendation(
            "close-1",
            "CLOSE",
            "opening_range_breakout",
            created_at,
            10.5,
            10.0,
            11.0,
            11.5,
            RecommendationState.ACTIVE,
            "장 마감 테스트",
        )
    )
    store.set_last_processed_bar("CLOSE", created_at, 10.3)
    observed_at = dt.datetime(2026, 7, 10, 16, 0, tzinfo=ZoneInfo("America/New_York"))

    finalized = run_kis_paper_watch.finalize_session_output(
        tmp_path,
        observed_at,
    )

    assert finalized == 1
    assert store.recommendations()[0].state is RecommendationState.TIME_EXIT
    assert "장 마감 종료" in (tmp_path / "recommendations_ko.md").read_text(encoding="utf-8")


def test_ranking_snapshot_appends_raw_sources_and_selection(tmp_path: Path) -> None:
    path = tmp_path / "kis_ranking_snapshots.csv"
    selected = KisRankedStock(
        "NAS",
        "FAST",
        "Fast Corp",
        10.0,
        0.12,
        9.99,
        10.01,
        500_000,
        5_000_000.0,
        200_000,
        1,
    )
    same_symbol_updown = KisRankedStock(
        "NAS",
        "FAST",
        "Fast Corp",
        10.0,
        0.12,
        9.99,
        10.01,
        500_000,
        4_900_000.0,
        200_000,
        2,
    )
    rejected = KisRankedStock(
        "NYS",
        "SLOW",
        "Slow Corp",
        20.0,
        0.02,
        19.98,
        20.02,
        10_000,
        200_000.0,
        100_000,
        2,
    )
    groups = (
        RankingGroup(
            RankingSource.UPDOWN,
            "NAS",
            (same_symbol_updown,),
        ),
        RankingGroup(
            RankingSource.VOLUME,
            "NAS",
            (selected,),
        ),
        RankingGroup(
            RankingSource.VOLUME,
            "NYS",
            (rejected,),
        ),
    )
    first_at = dt.datetime(2026, 7, 10, 9, 30, tzinfo=dt.UTC)

    run_kis_paper_scan.append_ranking_snapshot(
        path,
        run_kis_paper_scan.RankingSnapshot(first_at, groups, (selected,)),
    )
    run_kis_paper_scan.append_ranking_snapshot(
        path,
        run_kis_paper_scan.RankingSnapshot(
            first_at + dt.timedelta(minutes=1),
            groups,
            (selected,),
        ),
    )

    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    assert len(rows) == 6
    assert tuple(row["ranking_source"] for row in rows[:3]) == (
        "updown",
        "volume",
        "volume",
    )
    assert rows[0]["symbol"] == "FAST"
    assert rows[0]["selected"] == "True"
    assert rows[0]["selection_input"] == "False"
    assert rows[1]["selected"] == "True"
    assert rows[1]["selection_input"] == "True"
    assert rows[1]["dollar_volume"] == "5000000.0"
    assert rows[2]["selected"] == "False"
    assert rows[3]["observed_at"] == (first_at + dt.timedelta(minutes=1)).isoformat()


def test_watchlist_keeps_prior_candidates_for_the_same_session(tmp_path: Path) -> None:
    database = tmp_path / "paper.sqlite3"
    first = KisRankedStock(
        "NAS",
        "FIRST",
        "First Corp",
        10.0,
        0.1,
        9.99,
        10.01,
        500_000,
        5_000_000.0,
        200_000,
        1,
    )
    replacement = KisRankedStock(
        "NYS",
        "NEXT",
        "Next Corp",
        20.0,
        0.2,
        19.98,
        20.02,
        300_000,
        6_000_000.0,
        150_000,
        1,
    )
    observed_at = dt.datetime(
        2026,
        7,
        10,
        9,
        35,
        tzinfo=ZoneInfo("America/New_York"),
    )

    run_kis_paper_scan.track_candidates(database, observed_at, (first,))
    run_kis_paper_scan.track_candidates(
        database,
        observed_at + dt.timedelta(minutes=1),
        (replacement,),
    )

    tracked = run_kis_paper_scan.tracked_candidates(
        database,
        observed_at + dt.timedelta(minutes=1),
    )
    next_session = run_kis_paper_scan.tracked_candidates(
        database,
        observed_at + dt.timedelta(days=3),
    )
    assert tuple(stock.symbol for stock in tracked) == ("FIRST", "NEXT")
    assert next_session == ()

    followers = run_kis_paper_scan.unselected_tracked_candidates(
        (replacement,),
        tracked,
    )

    assert tuple(stock.symbol for stock in followers) == ("FIRST",)
