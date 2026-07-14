from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import run_paper_metrics
from trading_agent import metrics, metrics_report
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.store import PaperStore


def test_metrics_report_writes_cost_year_and_trade_outputs(tmp_path: Path) -> None:
    entered_2025 = dt.datetime(
        2025, 7, 10, 10, 0, tzinfo=ZoneInfo("America/New_York")
    )
    entered_2026 = dt.datetime(
        2026, 7, 10, 10, 0, tzinfo=ZoneInfo("America/New_York")
    )
    trades = (
        metrics.PaperTrade(
            "2025-win",
            "WIN",
            "opening_range_breakout",
            entered_2025,
            entered_2025 + dt.timedelta(minutes=5),
            10.0,
            11.0,
            0.1,
            RecommendationState.TARGET_2R,
            False,
        ),
        metrics.PaperTrade(
            "2026-loss",
            "LOSS",
            "opening_range_breakout",
            entered_2026,
            entered_2026 + dt.timedelta(minutes=5),
            10.0,
            9.5,
            -0.05,
            RecommendationState.TIME_EXIT,
            True,
        ),
    )

    metrics_report.write_metrics_report(tmp_path, trades)

    with (tmp_path / "paper_metrics.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        metrics_rows = tuple(csv.DictReader(handle))
    with (tmp_path / "paper_yearly_metrics.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        yearly_rows = tuple(csv.DictReader(handle))
    with (tmp_path / "paper_trades.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        trade_rows = tuple(csv.DictReader(handle))
    markdown = (tmp_path / "paper_metrics_ko.md").read_text(encoding="utf-8")
    assert tuple(int(row["side_cost_bps"]) for row in metrics_rows) == (5, 10, 20)
    assert all(row["trade_count"] == "2" for row in metrics_rows)
    assert len(yearly_rows) == 6
    assert {row["year"] for row in yearly_rows} == {"2025", "2026"}
    assert len(trade_rows) == 2
    assert trade_rows[1]["uses_close_fallback"] == "True"
    assert "QA·paper 표본" in markdown
    assert "bootstrap" in markdown
    assert "다중검정" in markdown


def test_metrics_cli_deduplicates_recommendations_across_databases(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sessions"
    _complete_store(source / "one" / "paper_recommendations.sqlite3", "same")
    _complete_store(source / "two" / "paper_recommendations.sqlite3", "same")
    output = tmp_path / "metrics"

    run_paper_metrics.main(str(source), str(output))

    with (output / "paper_trades.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        trades = tuple(csv.DictReader(handle))
    with (output / "paper_metrics.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        summaries = tuple(csv.DictReader(handle))
    assert len(trades) == 1
    assert all(row["trade_count"] == "1" for row in summaries)


def _complete_store(path: Path, recommendation_id: str) -> None:
    store = PaperStore(path)
    created_at = dt.datetime(
        2026, 7, 10, 10, 0, tzinfo=ZoneInfo("America/New_York")
    )
    recommendation = Recommendation(
        recommendation_id,
        "DUP",
        "opening_range_breakout",
        created_at,
        10.0,
        9.5,
        10.5,
        11.0,
        RecommendationState.SETUP,
        "metrics fixture",
    )
    store.save(recommendation)
    store.set_state(
        recommendation_id,
        RecommendationState.ACTIVE,
        created_at + dt.timedelta(minutes=1),
        10.0,
        "조건부 진입가 도달",
    )
    store.set_state(
        recommendation_id,
        RecommendationState.TARGET_2R,
        created_at + dt.timedelta(minutes=2),
        11.0,
        "2R 목표가 도달",
    )
