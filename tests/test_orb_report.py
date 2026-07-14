from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import run_orb_forward_metrics
from trading_agent import orb_report
from trading_agent.orb_models import OrbOutcome, OrbOutcomeStatus, OrbTestConfig
from trading_agent.ranking_journal import RANKING_FIELDS
from trading_agent.store import PaperStore


def test_orb_report_writes_cost_metrics_and_trade_audit(tmp_path: Path) -> None:
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
    outcome = OrbOutcome(
        config,
        observed_at,
        "NAS",
        "LOSS",
        0.1,
        5_000_000.0,
        20.0,
        True,
        OrbOutcomeStatus.STOPPED,
        observed_at,
        observed_at + dt.timedelta(minutes=1),
        observed_at + dt.timedelta(minutes=2),
        10.0,
        9.5,
        11.0,
        9.5,
        -0.05,
        True,
    )

    orb_report.write_orb_report(tmp_path, (outcome,))

    with (tmp_path / "orb_parameter_results.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        metrics = tuple(csv.DictReader(handle))
    with (tmp_path / "orb_trades.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        trades = tuple(csv.DictReader(handle))
    with (tmp_path / "orb_period_results.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        periods = tuple(csv.DictReader(handle))
    with (tmp_path / "orb_flatness_results.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        flatness = tuple(csv.DictReader(handle))
    markdown = (tmp_path / "orb_forward_report_ko.md").read_text(encoding="utf-8")
    assert len(metrics) == 3
    assert tuple(row["side_cost_bps"] for row in metrics) == ("5", "10", "20")
    assert metrics[0]["trade_count"] == "1"
    assert len(trades) == 1
    assert trades[0]["status"] == "stopped"
    assert len(periods) == 6
    period_metrics = {(row["period"], row["side_cost_bps"]): row for row in periods}
    assert period_metrics[("pre_2025", "5")]["trade_count"] == "0"
    assert period_metrics[("2025_plus", "5")]["trade_count"] == "1"
    assert period_metrics[("pre_2025", "5")]["average_return"] == ""
    assert len(flatness) == 3
    assert flatness[0]["neighbor_count"] == "0"
    assert flatness[0]["flat_positive_region"] == "False"
    assert "최대 10포지션" in markdown
    assert "다중검정" in markdown


def test_orb_forward_cli_writes_an_empty_parameter_grid(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    _ = (session / "kis_ranking_snapshots.csv").write_text(
        ",".join(RANKING_FIELDS) + "\n",
        encoding="utf-8",
    )
    _ = PaperStore(session / "paper_recommendations.sqlite3")
    output = tmp_path / "orb"

    run_orb_forward_metrics.main(str(session), str(output))

    with (output / "orb_parameter_results.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        metrics = tuple(csv.DictReader(handle))
    with (output / "orb_trades.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        trades = tuple(csv.DictReader(handle))
    with (output / "orb_period_results.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        periods = tuple(csv.DictReader(handle))
    with (output / "orb_flatness_results.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        flatness = tuple(csv.DictReader(handle))
    assert len(metrics) == 243
    assert all(row["trade_count"] == "0" for row in metrics)
    assert len(periods) == 486
    assert all(row["trade_count"] == "0" for row in periods)
    assert len(flatness) == 243
    assert all(row["eligible_neighbor_count"] == "0" for row in flatness)
    assert trades == ()
