from __future__ import annotations

import csv
from pathlib import Path

from trading_agent.orb_analysis import default_orb_grid
from trading_agent.orb_artifact_gate import audit_orb_report_artifacts
from trading_agent.orb_report import write_orb_report


def test_orb_artifact_gate_accepts_complete_period_and_flatness_contract(
    tmp_path: Path,
) -> None:
    write_orb_report(tmp_path, (), default_orb_grid())

    result = audit_orb_report_artifacts(tmp_path, expected_config_count=81)

    assert result.passed
    assert result.parameter_row_count == 243
    assert result.period_row_count == 486
    assert result.flatness_row_count == 243
    assert result.issues == ()


def test_orb_artifact_gate_rejects_missing_period_and_fake_zero_trade_metric(
    tmp_path: Path,
) -> None:
    write_orb_report(tmp_path, (), default_orb_grid())
    period_path = tmp_path / "orb_period_results.csv"
    with period_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames
    assert fieldnames is not None
    rows[0]["average_return"] = "0"
    rows.pop()
    with period_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    result = audit_orb_report_artifacts(tmp_path, expected_config_count=81)

    assert not result.passed
    assert "period:row_count:485!=486" in result.issues
    assert any(issue.startswith("period:zero_trade_nonblank:average_return:") for issue in result.issues)
