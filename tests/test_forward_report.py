from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import run_scanner_forward_metrics
from trading_agent import forward_report
from trading_agent.forward_outcomes import ForwardOutcome
from trading_agent.ranking_journal import RANKING_FIELDS
from trading_agent.store import PaperStore


def test_forward_report_separates_censored_and_complete_outcomes(
    tmp_path: Path,
) -> None:
    timestamp = dt.datetime(
        2026,
        7,
        10,
        9,
        35,
        tzinfo=ZoneInfo("America/New_York"),
    )
    complete = ForwardOutcome(
        timestamp,
        "NAS",
        "DONE",
        10.0,
        0.1,
        20.0,
        5_000_000.0,
        timestamp + dt.timedelta(minutes=1),
        10.0,
        384,
        True,
        0.01,
        0.02,
        0.03,
        0.1,
        0.2,
        -0.05,
    )
    censored = ForwardOutcome(
        timestamp,
        "NYS",
        "OPEN",
        20.0,
        0.05,
        30.0,
        1_000_000.0,
        None,
        None,
        0,
        False,
        None,
        None,
        None,
        None,
        None,
        None,
    )

    forward_report.write_forward_report(tmp_path, (complete, censored))

    with (tmp_path / "scanner_forward_outcomes.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        outcome_rows = tuple(csv.DictReader(handle))
    with (tmp_path / "scanner_threshold_summary.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        threshold_rows = tuple(csv.DictReader(handle))
    markdown = (tmp_path / "scanner_forward_report_ko.md").read_text(
        encoding="utf-8"
    )
    strict = next(
        row
        for row in threshold_rows
        if row["min_change_pct"] == "0.1"
        and row["min_dollar_volume"] == "5000000.0"
    )
    assert len(outcome_rows) == 2
    assert len(threshold_rows) == 16
    assert strict["complete_count"] == "1"
    assert strict["average_eod_return"] == "0.1"
    assert strict["average_eod_net_10bp"] != ""
    assert "중도절단" in markdown
    assert "다중검정" in markdown
    assert "KIS 랭킹 상위 표본" in markdown


def test_forward_metrics_cli_writes_empty_safe_outputs(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    _ = (session / "kis_ranking_snapshots.csv").write_text(
        ",".join(RANKING_FIELDS) + "\n",
        encoding="utf-8",
    )
    _ = PaperStore(session / "paper_recommendations.sqlite3")
    output = tmp_path / "metrics"

    run_scanner_forward_metrics.main(str(session), str(output))

    with (output / "scanner_forward_outcomes.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = tuple(csv.DictReader(handle))
    assert rows == ()
    assert (output / "scanner_threshold_summary.csv").is_file()
    assert "완전한 경로: 0건" in (
        output / "scanner_forward_report_ko.md"
    ).read_text(encoding="utf-8")
