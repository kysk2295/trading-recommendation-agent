from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent.alpaca_scanner_quality_models import (
    ScannerQualityOutcome,
    scanner_quality_grid,
)
from trading_agent.alpaca_scanner_quality_report import write_scanner_quality_report
from trading_agent.scanner_artifact_gate import audit_scanner_report_artifacts


def test_scanner_artifact_gate_accepts_complete_empty_grid_contract(
    tmp_path: Path,
) -> None:
    write_scanner_quality_report(tmp_path, (), scanner_quality_grid())

    result = audit_scanner_report_artifacts(tmp_path, expected_config_count=108)

    assert result.passed
    assert result.summary_row_count == 108
    assert result.outcome_row_count == 0
    assert result.yearly_row_count == 0
    assert result.issues == ()


def test_scanner_artifact_gate_rejects_missing_config_and_fake_empty_return(
    tmp_path: Path,
) -> None:
    write_scanner_quality_report(tmp_path, (), scanner_quality_grid())
    summary_path = tmp_path / "scanner_quality_summary.csv"
    with summary_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames
    assert fieldnames is not None
    rows[0]["average_eod_return"] = "0"
    rows.pop()
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    result = audit_scanner_report_artifacts(tmp_path, expected_config_count=108)

    assert not result.passed
    assert "summary:row_count:107!=108" in result.issues
    assert any(issue.startswith("summary:empty_selection_nonblank:average_eod_return:") for issue in result.issues)


def test_scanner_artifact_gate_rejects_more_than_ten_and_noncontiguous_ranks(
    tmp_path: Path,
) -> None:
    configs = scanner_quality_grid()
    config = configs[0]
    entry_at = dt.datetime(
        2026,
        6,
        12,
        9,
        31,
        tzinfo=ZoneInfo("America/New_York"),
    )
    outcomes = tuple(
        ScannerQualityOutcome(
            config,
            entry_at.date(),
            f"SYM{index:02d}",
            12 if index == 11 else index,
            0,
            False,
            entry_at,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        for index in range(1, 12)
    )
    write_scanner_quality_report(tmp_path, outcomes, configs)

    result = audit_scanner_report_artifacts(tmp_path, expected_config_count=108)

    assert not result.passed
    assert any(issue.startswith("outcome:portfolio_limit:") for issue in result.issues)
    assert any(issue.startswith("outcome:rank_sequence:") for issue in result.issues)


def test_scanner_artifact_gate_rejects_entry_at_scanner_cutoff(
    tmp_path: Path,
) -> None:
    configs = scanner_quality_grid()
    config = configs[0]
    cutoff = dt.datetime(
        2026,
        6,
        12,
        9,
        30,
        tzinfo=ZoneInfo("America/New_York"),
    )
    outcome = ScannerQualityOutcome(
        config,
        cutoff.date(),
        "LOOKAHEAD",
        1,
        0,
        False,
        cutoff,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    write_scanner_quality_report(tmp_path, (outcome,), configs)

    result = audit_scanner_report_artifacts(tmp_path, expected_config_count=108)

    assert not result.passed
    assert any(issue.startswith("outcome:entry_at_not_09_31:") for issue in result.issues)
