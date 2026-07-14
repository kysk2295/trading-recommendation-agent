from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import pytest

from trading_agent.risk_sensitivity import (
    RiskCandidate,
    RiskScreenFormatError,
    RiskSensitivityConfig,
    analyze_risk_sensitivity,
    load_risk_candidates,
    write_risk_sensitivity,
)


def test_sensitivity_recomputes_filters_then_selects_at_most_ten() -> None:
    observed_at = dt.datetime(2026, 7, 13, 5, 0, tzinfo=dt.UTC)
    candidates = (
        *(
            _candidate(observed_at, f"S{index:02d}", 0.20 - index / 100, 50.0)
            for index in range(12)
        ),
        _candidate(observed_at, "HALT", 0.50, 10.0, reason="공식 현재 거래정지"),
        _candidate(observed_at, "WIDE", 0.40, 130.0),
    )
    config = RiskSensitivityConfig(80.0, 10.0, 100.0)

    result = analyze_risk_sensitivity(candidates, (config,))

    assert result.summaries[0].candidate_count == 14
    assert result.summaries[0].hard_excluded_count == 1
    assert result.summaries[0].cost_eligible_count == 12
    assert result.summaries[0].selected_count == 10
    assert tuple(row.symbol for row in result.selections) == tuple(f"S{i:02d}" for i in range(10))


def test_csv_boundary_and_report_preserve_adjacent_grid_results(tmp_path: Path) -> None:
    source = tmp_path / "market_risk_screen.csv"
    _write_source(source)
    candidates = load_risk_candidates((source,))
    configs = (
        RiskSensitivityConfig(80.0, 10.0, 100.0),
        RiskSensitivityConfig(120.0, 20.0, 160.0),
    )

    result = analyze_risk_sensitivity(candidates, configs)
    output = tmp_path / "results"
    write_risk_sensitivity(output, result, (source,))

    assert tuple(row.cost_eligible_count for row in result.summaries) == (1, 2)
    assert tuple(round(row.retention_rate, 4) for row in result.summaries) == (0.25, 0.5)
    with (output / "market_risk_sensitivity.csv").open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["max_spread_bps"] == "80.0"
    report = (output / "market_risk_sensitivity_ko.md").read_text(encoding="utf-8")
    assert "수익성 백테스트가 아니다" in report
    assert "2개 인접 조합" in report


def test_csv_boundary_raises_typed_error_for_unknown_header(tmp_path: Path) -> None:
    source = tmp_path / "market_risk_screen.csv"
    _ = source.write_text("unknown\nvalue\n", encoding="utf-8")

    with pytest.raises(RiskScreenFormatError, match="헤더가 예상과 다릅니다"):
        _ = load_risk_candidates((source,))


def _candidate(
    observed_at: dt.datetime,
    symbol: str,
    change_pct: float,
    spread_bps: float,
    *,
    reason: str = "",
) -> RiskCandidate:
    return RiskCandidate(
        observed_at,
        "NAS",
        symbol,
        reason,
        change_pct,
        10.0,
        9.99,
        10.01,
        spread_bps,
        10_000_000.0,
    )


def _write_source(path: Path) -> None:
    header = (
        "observed_at",
        "exchange",
        "symbol",
        "selected",
        "reason",
        "change_pct",
        "price",
        "bid",
        "ask",
        "spread_bps",
        "estimated_round_trip_cost_bps",
        "dollar_volume",
    )
    rows = (
        ("2026-07-13T05:00:00+00:00", "NAS", "TIGHT", False, "", 0.2, 10, 9.98, 10.02, 40, 80, 10_000_000),
        ("2026-07-13T05:00:00+00:00", "NAS", "MID", False, "스프레드 초과", 0.19, 10, 9.96, 10.04, 90, 130, 9_000_000),
        (
            "2026-07-13T05:00:00+00:00", "NAS", "WIDE", False, "스프레드 초과",
            0.18, 10, 9.94, 10.06, 130, 170, 8_000_000,
        ),
        (
            "2026-07-13T05:00:00+00:00", "NAS", "HALT", False, "공식 현재 거래정지",
            0.4, 10, 9.99, 10.01, 20, 60, 7_000_000,
        ),
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
