from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import run_scanner_candidate_sensitivity
from trading_agent.market_risk import MARKET_RISK_HEADER
from trading_agent.risk_sensitivity import RiskCandidate
from trading_agent.scanner_sensitivity import (
    ScannerSensitivityConfig,
    analyze_scanner_sensitivity,
    scanner_sensitivity_grid,
    write_scanner_sensitivity,
)


def test_grid_reselects_ten_from_full_risk_eligible_population() -> None:
    observed_at = dt.datetime(2026, 7, 13, 14, 30, tzinfo=dt.UTC)
    candidates = (
        *(
            _candidate(
                observed_at,
                f"S{index:02d}",
                0.20 - index / 100,
                reason="" if index == 0 else "포트폴리오 한도",
            )
            for index in range(12)
        ),
        _candidate(observed_at, "REJECTED", 0.50, reason="유효 호가 없음"),
    )
    config = ScannerSensitivityConfig(0.04, 200.0, 500_000.0, 0.05)

    result = analyze_scanner_sensitivity(candidates, (config,))

    summary = result.summaries[0]
    assert summary.candidate_count == 13
    assert summary.risk_eligible_count == 12
    assert summary.feature_available_count == 12
    assert summary.threshold_eligible_count == 12
    assert summary.selected_count == 10
    assert tuple(row.symbol for row in result.selections) == tuple(f"S{i:02d}" for i in range(10))


def test_grid_applies_price_liquidity_and_volume_to_adv_before_selection() -> None:
    observed_at = dt.datetime(2026, 7, 13, 14, 30, tzinfo=dt.UTC)
    candidates = (
        _candidate(observed_at, "PASS", 0.10),
        _candidate(observed_at, "PRICE", 0.20, price=60.0),
        _candidate(observed_at, "DOLLARS", 0.19, dollar_volume=900_000.0),
        _candidate(observed_at, "VOLUME", 0.18, volume=50_000),
    )
    config = ScannerSensitivityConfig(0.08, 50.0, 1_000_000.0, 0.10)

    result = analyze_scanner_sensitivity(candidates, (config,))

    assert result.summaries[0].threshold_eligible_count == 1
    assert tuple(row.symbol for row in result.selections) == ("PASS",)


def test_legacy_rows_are_disclosed_as_feature_missing_and_report_is_not_performance(
    tmp_path: Path,
) -> None:
    observed_at = dt.datetime(2026, 7, 13, 14, 30, tzinfo=dt.UTC)
    legacy = RiskCandidate(
        observed_at,
        "NAS",
        "LEGACY",
        "",
        0.10,
        10.0,
        9.99,
        10.01,
        20.0,
        5_000_000.0,
    )

    result = analyze_scanner_sensitivity((legacy,), scanner_sensitivity_grid())
    write_scanner_sensitivity(tmp_path, result)

    assert len(result.summaries) == 81
    assert result.summaries[0].feature_missing_count == 1
    assert result.selections == ()
    with (tmp_path / "scanner_candidate_sensitivity.csv").open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    assert len(rows) == 81
    report = (tmp_path / "scanner_candidate_sensitivity_ko.md").read_text(encoding="utf-8")
    assert "수익성·후행수익 분석이 아니다" in report
    assert "opening gap: 전체 후보 시가 미제공" in report


def test_cli_reads_current_risk_screen_and_writes_81_combinations(tmp_path: Path) -> None:
    source = tmp_path / "market_risk_screen.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MARKET_RISK_HEADER)
        writer.writerow(
            (
                "2026-07-13T14:30:00+00:00", "NAS", "PASS", True, "", 0.10,
                10.0, 9.99, 10.01, 20.0, 60.0, 5_000_000.0, 100_000, 1_000_000, 0.1,
            )
        )
    output = tmp_path / "output"

    run_scanner_candidate_sensitivity.main(str(source), str(output))

    with (output / "scanner_candidate_sensitivity.csv").open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    assert len(rows) == 81
    assert max(int(row["selected_count"]) for row in rows) == 1


def _candidate(
    observed_at: dt.datetime,
    symbol: str,
    change_pct: float,
    *,
    reason: str = "",
    price: float = 10.0,
    dollar_volume: float = 5_000_000.0,
    volume: int = 100_000,
) -> RiskCandidate:
    return RiskCandidate(
        observed_at,
        "NAS",
        symbol,
        reason,
        change_pct,
        price,
        price - 0.01,
        price + 0.01,
        20.0,
        dollar_volume,
        volume,
        1_000_000,
    )
