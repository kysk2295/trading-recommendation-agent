from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import typer

import run_alpaca_pilot_orb
from trading_agent.alpaca_orb_archive import (
    AlpacaOrbArchiveConfig,
    AlpacaOrbArchiveError,
    analyze_alpaca_orb_grid,
)
from trading_agent.alpaca_scanner_quality_gate import (
    ScannerQualityGateResult,
    write_scanner_quality_gate,
)
from trading_agent.alpaca_scanner_quality_models import scanner_quality_grid
from trading_agent.alpaca_scanner_quality_report import write_scanner_quality_report
from trading_agent.orb_artifact_gate import OrbArtifactGateResult
from trading_agent.orb_models import OrbOutcomeStatus, OrbTestConfig
from trading_agent.session_date_range import SessionDateRange


def test_alpaca_orb_archive_uses_bar_close_observation_and_next_minute_entry(
    tmp_path: Path,
) -> None:
    # Given
    _write_complete_session(tmp_path)
    config = OrbTestConfig(5, 5.0, 1.5, 1.0, 1.0)

    # When
    outcomes = analyze_alpaca_orb_grid(
        tmp_path,
        (config,),
        AlpacaOrbArchiveConfig(assumed_spread_bps=20.0),
    )

    # Then
    assert len(outcomes) == 1
    assert outcomes[0].status is OrbOutcomeStatus.TARGET
    assert outcomes[0].signal_at == dt.datetime(
        2026,
        6,
        12,
        9,
        36,
        tzinfo=ZoneInfo("America/New_York"),
    )
    assert outcomes[0].entry_at == dt.datetime(
        2026,
        6,
        12,
        9,
        37,
        tzinfo=ZoneInfo("America/New_York"),
    )
    assert outcomes[0].portfolio_selected


def test_alpaca_orb_archive_ignores_sessions_outside_fixed_window(tmp_path: Path) -> None:
    # Given
    _write_complete_session(tmp_path)
    outside = tmp_path / "staged_sessions/2025/01/02/session_old.metadata.json"
    outside.parent.mkdir(parents=True)
    outside.write_text(
        json.dumps(
            {
                "status": "complete",
                "session_date": "2025-01-02",
                "scanner_cutoff": "09:35:00",
                "selected_symbol_count": 1,
                "selected_symbols": ["OLD"],
                "candidate_bar_count": 1,
            }
        ),
        encoding="utf-8",
    )
    session_range = SessionDateRange(dt.date(2026, 6, 12), dt.date(2026, 6, 12))

    # When
    outcomes = analyze_alpaca_orb_grid(
        tmp_path,
        (OrbTestConfig(5, 5.0, 1.5, 1.0, 1.0),),
        session_range=session_range,
    )

    # Then
    assert len(outcomes) == 1
    assert outcomes[0].observed_at.date() == dt.date(2026, 6, 12)


def test_alpaca_pilot_orb_cli_writes_audit_and_cost_grid(tmp_path: Path) -> None:
    # Given
    _write_complete_session(tmp_path)
    output = tmp_path / "results"
    scanner_gate = _write_scanner_gate(tmp_path, passed=True)

    # When
    run_alpaca_pilot_orb.main(
        str(tmp_path),
        str(output),
        minimum_sessions=1,
        scanner_gate_path=str(scanner_gate),
    )

    # Then
    audit = json.loads((output / "pilot_audit.json").read_text(encoding="utf-8"))
    assert audit["passed"] is True
    with (output / "orb_parameter_results.csv").open(encoding="utf-8", newline="") as handle:
        parameters = tuple(csv.DictReader(handle))
    assert len(parameters) == 243
    assert (output / "alpaca_orb_pilot_report_ko.md").is_file()
    assert "판정: PASS" in (output / "pilot_gate_ko.md").read_text(encoding="utf-8")
    pilot_gate = json.loads((output / "pilot_gate.json").read_text(encoding="utf-8"))
    assert pilot_gate["orb_artifacts_passed"] is True
    assert pilot_gate["orb_parameter_row_count"] == 243
    assert pilot_gate["orb_period_row_count"] == 486
    assert pilot_gate["orb_flatness_row_count"] == 243
    assert pilot_gate["scanner_artifacts_passed"] is True
    assert pilot_gate["scanner_summary_row_count"] == 108
    assert not (output / "orb_forward_report_ko.md").exists()


def test_alpaca_pilot_orb_cli_propagates_fixed_window(tmp_path: Path) -> None:
    # Given
    _write_complete_session(tmp_path)
    outside = tmp_path / "staged_sessions/2025/01/02/session_broken.metadata.json"
    outside.parent.mkdir(parents=True)
    outside.write_text("not-json", encoding="utf-8")
    output = tmp_path / "results"
    scanner_gate = _write_scanner_gate(tmp_path, passed=True)

    # When
    run_alpaca_pilot_orb.main(
        str(tmp_path),
        str(output),
        minimum_sessions=1,
        scanner_gate_path=str(scanner_gate),
        start=dt.date(2026, 6, 12),
        end=dt.date(2026, 6, 12),
    )

    # Then
    audit = json.loads((output / "pilot_audit.json").read_text(encoding="utf-8"))
    assert audit["session_start"] == "2026-06-12"
    assert audit["session_end"] == "2026-06-12"


def test_alpaca_pilot_orb_blocks_failed_scanner_gate(tmp_path: Path) -> None:
    # Given
    _write_complete_session(tmp_path)
    output = tmp_path / "results"
    scanner_gate = _write_scanner_gate(tmp_path, passed=False)

    # When
    with pytest.raises(typer.Exit) as raised:
        run_alpaca_pilot_orb.main(
            str(tmp_path),
            str(output),
            minimum_sessions=1,
            scanner_gate_path=str(scanner_gate),
        )

    # Then
    assert raised.value.exit_code == 2
    assert "판정: FAIL" in (output / "pilot_gate_ko.md").read_text(encoding="utf-8")
    assert (output / "pilot_gate.json").is_file()
    assert not (output / "orb_parameter_results.csv").exists()


def test_alpaca_pilot_orb_reaudits_and_blocks_missing_scanner_artifact(
    tmp_path: Path,
) -> None:
    _write_complete_session(tmp_path)
    output = tmp_path / "results"
    scanner_gate = _write_scanner_gate(tmp_path, passed=True)
    (scanner_gate.parent / "scanner_quality_summary.csv").unlink()

    with pytest.raises(typer.Exit) as raised:
        run_alpaca_pilot_orb.main(
            str(tmp_path),
            str(output),
            minimum_sessions=1,
            scanner_gate_path=str(scanner_gate),
        )

    assert raised.value.exit_code == 2
    pilot_gate = json.loads((output / "pilot_gate.json").read_text(encoding="utf-8"))
    assert pilot_gate["scanner_artifacts_passed"] is False
    assert "scanner_artifact:summary:missing:scanner_quality_summary.csv" in pilot_gate["issues"]
    assert not (output / "orb_parameter_results.csv").exists()


def test_alpaca_pilot_orb_blocks_relaxed_scanner_thresholds(
    tmp_path: Path,
) -> None:
    # Given
    _write_complete_session(tmp_path)
    output = tmp_path / "results"
    scanner_gate = _write_scanner_gate(tmp_path, passed=True, relaxed=True)

    # When
    with pytest.raises(typer.Exit) as raised:
        run_alpaca_pilot_orb.main(
            str(tmp_path),
            str(output),
            minimum_sessions=1,
            scanner_gate_path=str(scanner_gate),
        )

    # Then
    assert raised.value.exit_code == 2
    pilot_gate = json.loads((output / "pilot_gate.json").read_text(encoding="utf-8"))
    assert pilot_gate["scanner_thresholds_sufficient"] is False
    assert "scanner:minimum_path_coverage:0.000000<0.800000" in pilot_gate["issues"]
    assert not (output / "orb_parameter_results.csv").exists()


def test_alpaca_pilot_orb_blocks_invalid_report_artifact_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_complete_session(tmp_path)
    output = tmp_path / "results"
    scanner_gate = _write_scanner_gate(tmp_path, passed=True)
    invalid = OrbArtifactGateResult(
        False,
        243,
        485,
        243,
        243,
        486,
        243,
        ("period:row_count:485!=486",),
    )
    monkeypatch.setattr(
        run_alpaca_pilot_orb,
        "audit_orb_report_artifacts",
        lambda *_args, **_kwargs: invalid,
    )

    with pytest.raises(typer.Exit) as raised:
        run_alpaca_pilot_orb.main(
            str(tmp_path),
            str(output),
            minimum_sessions=1,
            scanner_gate_path=str(scanner_gate),
        )

    assert raised.value.exit_code == 2
    pilot_gate = json.loads((output / "pilot_gate.json").read_text(encoding="utf-8"))
    assert pilot_gate["orb_artifacts_passed"] is False
    assert "orb_artifact:period:row_count:485!=486" in pilot_gate["issues"]


def test_alpaca_orb_archive_rejects_non_opening_scanner_cutoff(
    tmp_path: Path,
) -> None:
    # Given
    _write_complete_session(tmp_path)
    staged = tmp_path / "staged_sessions/2026/06/12/session_demo.metadata.json"
    metadata = json.loads(staged.read_text(encoding="utf-8"))
    metadata["scanner_cutoff"] = "09:35:00"
    staged.write_text(json.dumps(metadata), encoding="utf-8")
    archive = tmp_path / "candidate_minutes/2026/06/12/archive_candidate/session.metadata.json"
    archive_metadata = json.loads(archive.read_text(encoding="utf-8"))
    archive_metadata["window_start"] = "09:35:00"
    archive.write_text(json.dumps(archive_metadata), encoding="utf-8")

    # When/Then
    with pytest.raises(AlpacaOrbArchiveError, match="09:30"):
        analyze_alpaca_orb_grid(
            tmp_path,
            (OrbTestConfig(5, 5.0, 1.5, 1.0, 1.0),),
        )


def _write_complete_session(root: Path) -> None:
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
                "universe_symbol_count": 1,
                "selected_symbol_count": 1,
                "selected_symbols": ["MOVE"],
                "scanner_bar_count": 1,
                "candidate_bar_count": 390,
                "selection_uses_bars_strictly_before_cutoff": True,
            }
        ),
        encoding="utf-8",
    )
    decisions = root / "scanner_decisions" / date_path / "scanner_decisions_demo.csv.gz"
    _write_rows(
        decisions,
        (
            {
                "symbol": "MOVE",
                "selected": "True",
                "last_timestamp": "2026-06-12T09:29:00-04:00",
                "change_pct": "0.10",
                "dollar_volume": "5000000",
            },
        ),
    )
    scanner = root / "scanner_minutes" / date_path / "archive_scanner"
    scanner.mkdir(parents=True)
    (scanner / "session.metadata.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "session_date": "2026-06-12",
                "bar_count": 1,
                "symbol_count": 1,
                "window_start": "04:00:00",
                "window_end": "09:30:00",
            }
        ),
        encoding="utf-8",
    )
    _write_rows(
        scanner / "batch_00000.csv.gz",
        (_bar_row(dt.datetime(2026, 6, 12, 13, 29, tzinfo=dt.UTC), high=10.0, low=9.9, close=10.0, volume=100),),
    )
    archive = root / "candidate_minutes" / date_path / "archive_candidate"
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
    start = dt.datetime(2026, 6, 12, 13, 30, tzinfo=dt.UTC)
    bars = tuple(_bar(start + dt.timedelta(minutes=index), index) for index in range(390))
    _write_rows(archive / "batch_00000.csv.gz", bars)


def _write_scanner_gate(
    root: Path,
    *,
    passed: bool,
    relaxed: bool = False,
) -> Path:
    output = root / "scanner_gate"
    write_scanner_quality_report(output, (), scanner_quality_grid())
    write_scanner_quality_gate(
        output,
        ScannerQualityGateResult(
            passed,
            100,
            100 if passed else 0,
            1.0 if passed else 0.0,
            0.0 if relaxed else 0.8,
            0 if relaxed else 100,
            () if passed else ("path_coverage:0.000000<0.800000",),
        ),
    )
    return output / "scanner_quality_gate.json"


def _bar(timestamp: dt.datetime, index: int) -> dict[str, str]:
    if index == 5:
        return _bar_row(timestamp, high=10.20, low=9.90, close=10.15, volume=200)
    if index == 7:
        return _bar_row(timestamp, high=10.30, low=10.00, close=10.25, volume=150)
    return _bar_row(timestamp, high=10.00, low=9.80, close=9.95, volume=100)


def _bar_row(
    timestamp: dt.datetime,
    *,
    high: float,
    low: float,
    close: float,
    volume: int,
) -> dict[str, str]:
    return {
        "symbol": "MOVE",
        "timestamp": timestamp.isoformat(),
        "open": "9.95",
        "high": str(high),
        "low": str(low),
        "close": str(close),
        "volume": str(volume),
        "trade_count": "100",
        "vwap": "10.0",
    }


def _write_rows(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
