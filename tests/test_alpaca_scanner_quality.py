from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import typer

import run_alpaca_pilot_scanner
from trading_agent.alpaca_scanner_quality import analyze_alpaca_scanner_quality
from trading_agent.alpaca_scanner_quality_models import (
    AlpacaScannerQualityError,
    ScannerQualityConfig,
    scanner_quality_grid,
)
from trading_agent.alpaca_scanner_quality_report import write_scanner_quality_report
from trading_agent.scanner_artifact_gate import ScannerArtifactGateResult
from trading_agent.session_date_range import SessionDateRange


def test_scanner_quality_reselects_from_all_decisions_and_censors_missing_paths(
    tmp_path: Path,
) -> None:
    # Given
    _write_session(tmp_path)
    loose = ScannerQualityConfig(0.02, 0.5, 100.0, 250_000.0, 0.01)
    low_price = ScannerQualityConfig(0.02, 0.5, 20.0, 250_000.0, 0.01)

    # When
    outcomes = analyze_alpaca_scanner_quality(tmp_path, (loose, low_price))

    # Then
    assert tuple(row.symbol for row in outcomes) == ("ALT", "BASE", "ALT")
    assert not outcomes[0].complete
    assert outcomes[0].entry is None
    assert outcomes[1].complete
    assert outcomes[1].entry_at == dt.datetime(
        2026,
        6,
        12,
        9,
        31,
        tzinfo=ZoneInfo("America/New_York"),
    )
    assert outcomes[1].entry == 10.0
    assert outcomes[1].return_5m == 0.01
    assert outcomes[1].eod_return == 0.02


def test_scanner_quality_grid_covers_adjacent_thresholds() -> None:
    # Given/When
    configs = scanner_quality_grid()

    # Then
    assert len(configs) == 108
    assert {row.min_change_pct for row in configs} == {0.02, 0.04, 0.06, 0.08}
    assert {row.max_price for row in configs} == {20.0, 50.0, 100.0}
    assert {row.min_dollar_volume for row in configs} == {250_000.0, 500_000.0, 1_000_000.0}
    assert {row.min_adv_fraction for row in configs} == {0.01, 0.05, 0.10}


def test_scanner_quality_rejects_non_opening_cutoff(tmp_path: Path) -> None:
    # Given
    _write_session(tmp_path)
    staged = tmp_path / "staged_sessions/2026/06/12/session_demo.metadata.json"
    metadata = json.loads(staged.read_text(encoding="utf-8"))
    metadata["scanner_cutoff"] = "09:35:00"
    staged.write_text(json.dumps(metadata), encoding="utf-8")

    # When/Then
    with pytest.raises(AlpacaScannerQualityError, match="09:30"):
        analyze_alpaca_scanner_quality(
            tmp_path,
            (ScannerQualityConfig(0.02, 0.5, 100.0, 250_000.0, 0.01),),
        )


def test_scanner_quality_ignores_sessions_outside_fixed_window(tmp_path: Path) -> None:
    # Given
    _write_session(tmp_path)
    outside = tmp_path / "staged_sessions/2025/01/02/session_old.metadata.json"
    outside.parent.mkdir(parents=True)
    outside.write_text(
        json.dumps(
            {
                "status": "complete",
                "session_date": "2025-01-02",
                "scanner_cutoff": "09:35:00",
                "selected_symbol_count": 1,
                "candidate_bar_count": 1,
            }
        ),
        encoding="utf-8",
    )
    session_range = SessionDateRange(dt.date(2026, 6, 12), dt.date(2026, 6, 12))

    # When
    outcomes = analyze_alpaca_scanner_quality(
        tmp_path,
        (ScannerQualityConfig(0.02, 0.5, 20.0, 250_000.0, 0.01),),
        session_range=session_range,
    )

    # Then
    assert len(outcomes) == 1
    assert {row.session_date for row in outcomes} == {dt.date(2026, 6, 12)}


def test_scanner_quality_report_keeps_censored_paths_out_of_returns(tmp_path: Path) -> None:
    # Given
    source = tmp_path / "source"
    output = tmp_path / "output"
    _write_session(source)
    configs = (
        ScannerQualityConfig(0.02, 0.5, 100.0, 250_000.0, 0.01),
        ScannerQualityConfig(0.02, 0.5, 20.0, 250_000.0, 0.01),
    )
    outcomes = analyze_alpaca_scanner_quality(source, configs)

    # When
    write_scanner_quality_report(output, outcomes, configs)

    # Then
    with (output / "scanner_quality_summary.csv").open(encoding="utf-8", newline="") as handle:
        summaries = {float(row["max_price"]): row for row in csv.DictReader(handle)}
    assert summaries[20.0]["selection_count"] == "1"
    assert summaries[20.0]["complete_count"] == "0"
    assert summaries[20.0]["average_eod_return"] == ""
    assert summaries[100.0]["selection_count"] == "2"
    assert summaries[100.0]["complete_count"] == "1"
    assert summaries[100.0]["path_coverage_rate"] == "0.5"
    assert summaries[100.0]["average_eod_return"] == "0.02"
    report = (output / "scanner_quality_report_ko.md").read_text(encoding="utf-8")
    assert "사후 필터링" in report
    assert "수익 0" in report


def test_pilot_scanner_cli_writes_audited_grid(tmp_path: Path) -> None:
    # Given
    source = tmp_path / "source"
    output = tmp_path / "output"
    _write_session(source)

    # When
    run_alpaca_pilot_scanner.main(
        str(source),
        str(output),
        minimum_sessions=1,
        minimum_path_coverage=0.5,
        minimum_complete_candidate_days=1,
    )

    # Then
    audit = json.loads((output / "pilot_audit.json").read_text(encoding="utf-8"))
    assert audit["passed"] is True
    with (output / "scanner_quality_summary.csv").open(encoding="utf-8", newline="") as handle:
        summaries = tuple(csv.DictReader(handle))
    assert len(summaries) == 108
    assert (output / "scanner_quality_report_ko.md").is_file()
    gate = json.loads((output / "scanner_quality_gate.json").read_text(encoding="utf-8"))
    assert gate["passed"] is True
    assert gate["unique_candidate_days"] == 2
    assert gate["complete_candidate_days"] == 1
    assert gate["path_coverage"] == 0.5


def test_pilot_scanner_cli_propagates_fixed_window(tmp_path: Path) -> None:
    # Given
    source = tmp_path / "source"
    output = tmp_path / "output"
    _write_session(source)
    outside = source / "staged_sessions/2025/01/02/session_broken.metadata.json"
    outside.parent.mkdir(parents=True)
    outside.write_text("not-json", encoding="utf-8")

    # When
    run_alpaca_pilot_scanner.main(
        str(source),
        str(output),
        minimum_sessions=1,
        minimum_path_coverage=0.5,
        minimum_complete_candidate_days=1,
        start=dt.date(2026, 6, 12),
        end=dt.date(2026, 6, 12),
    )

    # Then
    audit = json.loads((output / "pilot_audit.json").read_text(encoding="utf-8"))
    assert audit["session_start"] == "2026-06-12"
    assert audit["session_end"] == "2026-06-12"


def test_pilot_scanner_cli_blocks_invalid_report_artifact_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    _write_session(source)
    invalid = ScannerArtifactGateResult(
        False,
        107,
        3,
        2,
        108,
        ("summary:row_count:107!=108",),
    )
    monkeypatch.setattr(
        run_alpaca_pilot_scanner,
        "audit_scanner_report_artifacts",
        lambda *_args, **_kwargs: invalid,
    )

    with pytest.raises(typer.Exit) as raised:
        run_alpaca_pilot_scanner.main(
            str(source),
            str(output),
            minimum_sessions=1,
            minimum_path_coverage=0.5,
            minimum_complete_candidate_days=1,
        )

    assert raised.value.exit_code == 2
    gate = json.loads((output / "scanner_quality_gate.json").read_text(encoding="utf-8"))
    assert gate["passed"] is False
    assert "artifact:summary:row_count:107!=108" in gate["issues"]


def _write_session(root: Path) -> None:
    date_path = Path("2026/06/12")
    staged = root / "staged_sessions" / date_path / "session_demo.metadata.json"
    staged.parent.mkdir(parents=True)
    staged.write_text(
        json.dumps(
            {
                "status": "complete",
                "session_date": "2026-06-12",
                "scanner_cutoff": "09:30:00",
                "reference_source": "range_cache",
                "universe_symbol_count": 2,
                "selected_symbol_count": 1,
                "selected_symbols": ["BASE"],
                "scanner_bar_count": 2,
                "candidate_bar_count": 390,
                "selection_uses_bars_strictly_before_cutoff": True,
            }
        ),
        encoding="utf-8",
    )
    _write_rows(
        root / "scanner_decisions" / date_path / "scanner_decisions_demo.csv.gz",
        (
            _decision("BASE", True, 50.0, 0.20, 2_000_000.0, 0.20),
            _decision("ALT", False, 10.0, 0.10, 1_000_000.0, 0.10),
        ),
    )
    scanner = root / "scanner_minutes" / date_path / "archive_demo"
    scanner.mkdir(parents=True)
    (scanner / "session.metadata.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "session_date": "2026-06-12",
                "bar_count": 2,
                "symbol_count": 2,
                "window_start": "04:00:00",
                "window_end": "09:30:00",
            }
        ),
        encoding="utf-8",
    )
    _write_rows(
        scanner / "batch_00000.csv.gz",
        (
            {**_bar(dt.datetime(2026, 6, 12, 9, 29, tzinfo=ZoneInfo("America/New_York")), 0), "symbol": "BASE"},
            {**_bar(dt.datetime(2026, 6, 12, 9, 29, tzinfo=ZoneInfo("America/New_York")), 0), "symbol": "ALT"},
        ),
    )
    archive = root / "candidate_minutes" / date_path / "archive_demo"
    archive.mkdir(parents=True)
    (archive / "session.metadata.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "session_date": "2026-06-12",
                "bar_count": 390,
                "symbol_count": 1,
                "window_start": "09:30:00",
                "window_end": "20:00:00",
            }
        ),
        encoding="utf-8",
    )
    start = dt.datetime(2026, 6, 12, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    _write_rows(
        archive / "batch_00000.csv.gz",
        tuple(_bar(start + dt.timedelta(minutes=index), index) for index in range(390)),
    )


def _decision(
    symbol: str,
    selected: bool,
    price: float,
    change_pct: float,
    dollar_volume: float,
    adv_fraction: float,
) -> dict[str, str]:
    return {
        "symbol": symbol,
        "selected": str(selected),
        "rank": "1" if selected else "",
        "reason": "selected" if selected else "candidate_cap",
        "last_timestamp": "2026-06-12T09:29:00-04:00",
        "price": str(price),
        "known_gap_pct": str(change_pct),
        "change_pct": str(change_pct),
        "observed_volume": "100000",
        "dollar_volume": str(dollar_volume),
        "adv_fraction": str(adv_fraction),
        "prior_close": "10.0",
        "average_volume": "1000000",
        "history_sessions": "20",
    }


def _bar(timestamp: dt.datetime, index: int) -> dict[str, str]:
    close = 10.1 if index == 5 else 10.2 if index == 389 else 10.0
    return {
        "symbol": "BASE",
        "timestamp": timestamp.isoformat(),
        "open": "10.0",
        "high": str(max(10.0, close)),
        "low": "9.9",
        "close": str(close),
        "volume": "1000",
        "trade_count": "100",
        "vwap": "10.0",
    }


def _write_rows(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
