from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
from pathlib import Path

from trading_agent.alpaca_pilot_audit import audit_staged_pilot
from trading_agent.session_date_range import SessionDateRange


def test_pilot_audit_passes_complete_causal_session(tmp_path: Path) -> None:
    # Given
    _write_session(tmp_path, decision_timestamp="2026-06-12T09:29:00-04:00")

    # When
    result = audit_staged_pilot(tmp_path, minimum_sessions=1)

    # Then
    assert result.passed
    assert result.session_count == 1
    assert result.candidate_duplicate_count == 0
    assert result.temporal_violation_count == 0
    assert result.incomplete_artifact_count == 0


def test_pilot_audit_rejects_cutoff_duplicate_and_partial_file(tmp_path: Path) -> None:
    # Given
    candidate_path = _write_session(
        tmp_path,
        decision_timestamp="2026-06-12T09:30:00-04:00",
    )
    rows = tuple(_read_rows(candidate_path))
    _write_rows(candidate_path, (*rows, rows[0]))
    (tmp_path / "unfinished.csv.gz.part").write_text("partial", encoding="utf-8")

    # When
    result = audit_staged_pilot(tmp_path, minimum_sessions=1)

    # Then
    assert not result.passed
    assert result.candidate_duplicate_count == 1
    assert result.temporal_violation_count == 1
    assert result.incomplete_artifact_count == 1


def test_pilot_audit_rejects_candidate_bar_before_new_york_cutoff(tmp_path: Path) -> None:
    # Given
    candidate_path = _write_session(
        tmp_path,
        decision_timestamp="2026-06-12T09:29:00-04:00",
    )
    rows = _read_rows(candidate_path)
    rows[0]["timestamp"] = "2026-06-12T13:29:00+00:00"
    _write_rows(candidate_path, tuple(rows))

    # When
    result = audit_staged_pilot(tmp_path, minimum_sessions=1)

    # Then
    assert not result.passed
    assert result.temporal_violation_count == 1


def test_pilot_audit_does_not_count_market_holiday_metadata(tmp_path: Path) -> None:
    # Given
    _write_session(tmp_path, decision_timestamp="2026-06-12T09:29:00-04:00")
    metadata_path = tmp_path / "staged_sessions/2026/06/12/session_demo.metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["session_date"] = "2026-07-03"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    # When
    result = audit_staged_pilot(tmp_path, minimum_sessions=1)

    # Then
    assert not result.passed
    assert result.session_count == 0
    assert "non_market_session:2026-07-03" in result.issues


def test_pilot_audit_accepts_grid_union_archive_beyond_base_selection(
    tmp_path: Path,
) -> None:
    # Given
    candidate_path = _write_session(
        tmp_path,
        decision_timestamp="2026-06-12T09:29:00-04:00",
    )
    metadata_path = tmp_path / "staged_sessions/2026/06/12/session_demo.metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.update(
        {
            "selected_symbol_count": 2,
            "selected_symbols": ["ALT", "MOVE"],
            "base_selected_symbol_count": 1,
            "base_selected_symbols": ["MOVE"],
            "candidate_selection_contract": "base_plus_scanner_grid_top_10_union",
            "scanner_grid_config_count": 108,
            "scanner_grid_portfolio_limit": 10,
            "candidate_bar_count": 4,
        }
    )
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    decision_path = tmp_path / "scanner_decisions/2026/06/12/scanner_decisions_demo.csv.gz"
    decision_rows = _read_rows(decision_path)
    decision_rows.append(
        {
            "symbol": "ALT",
            "selected": "False",
            "last_timestamp": "2026-06-12T09:29:00-04:00",
            "price": "5.0",
            "change_pct": "0.05",
            "dollar_volume": "500000",
            "adv_fraction": "0.05",
        }
    )
    _write_rows(decision_path, tuple(decision_rows))
    candidate_rows = _read_rows(candidate_path)
    candidate_rows.extend(
        (
            _bar("ALT", "2026-06-12T13:30:00+00:00"),
            _bar("ALT", "2026-06-12T13:31:00+00:00"),
        )
    )
    _write_rows(candidate_path, tuple(candidate_rows))
    candidate_metadata = candidate_path.parent / "session.metadata.json"
    archive = json.loads(candidate_metadata.read_text(encoding="utf-8"))
    archive["symbol_count"] = 2
    archive["bar_count"] = 4
    candidate_metadata.write_text(json.dumps(archive), encoding="utf-8")

    # When
    result = audit_staged_pilot(tmp_path, minimum_sessions=1)

    # Then
    assert result.passed
    assert result.selected_symbol_count == 2


def test_pilot_audit_isolates_fixed_window_from_other_staged_data(tmp_path: Path) -> None:
    # Given
    _write_session(tmp_path, decision_timestamp="2026-06-12T09:29:00-04:00")
    outside = tmp_path / "staged_sessions/2025/01/02/session_broken.metadata.json"
    outside.parent.mkdir(parents=True)
    outside.write_text("not-json", encoding="utf-8")
    partial = tmp_path / "candidate_minutes/2025/01/02/archive_old/batch_00000.csv.gz.part"
    partial.parent.mkdir(parents=True)
    partial.write_text("partial", encoding="utf-8")
    session_range = SessionDateRange(dt.date(2026, 6, 12), dt.date(2026, 6, 12))

    # When
    result = audit_staged_pilot(
        tmp_path,
        minimum_sessions=1,
        session_range=session_range,
    )

    # Then
    assert result.passed
    assert result.session_count == 1
    assert result.incomplete_artifact_count == 0
    assert result.session_start == "2026-06-12"
    assert result.session_end == "2026-06-12"


def _write_session(root: Path, decision_timestamp: str) -> Path:
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
                "candidate_bar_count": 2,
                "selection_uses_bars_strictly_before_cutoff": True,
            }
        ),
        encoding="utf-8",
    )
    decision = root / "scanner_decisions" / date_path / "scanner_decisions_demo.csv.gz"
    _write_rows(
        decision,
        (
            {
                "symbol": "MOVE",
                "selected": "True",
                "last_timestamp": decision_timestamp,
                "price": "10.0",
                "change_pct": "0.10",
                "dollar_volume": "1000000",
                "adv_fraction": "0.10",
            },
        ),
    )
    scanner = root / "scanner_minutes" / date_path / "archive_scan"
    _write_archive_metadata(scanner, "04:00:00", "09:30:00", 1, 1)
    _write_rows(
        scanner / "batch_00000.csv.gz",
        (_bar("MOVE", "2026-06-12T13:29:00+00:00"),),
    )
    candidate = root / "candidate_minutes" / date_path / "archive_candidates"
    _write_archive_metadata(candidate, "09:30:00", "20:00:00", 1, 2)
    candidate_path = candidate / "batch_00000.csv.gz"
    _write_rows(
        candidate_path,
        (
            _bar("MOVE", "2026-06-12T13:30:00+00:00"),
            _bar("MOVE", "2026-06-12T13:31:00+00:00"),
        ),
    )
    return candidate_path


def _write_archive_metadata(
    archive: Path,
    window_start: str,
    window_end: str,
    symbol_count: int,
    bar_count: int,
) -> None:
    archive.mkdir(parents=True)
    (archive / "session.metadata.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "session_date": "2026-06-12",
                "bar_count": bar_count,
                "symbol_count": symbol_count,
                "window_start": window_start,
                "window_end": window_end,
            }
        ),
        encoding="utf-8",
    )


def _write_rows(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = tuple(rows[0])
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _bar(symbol: str, timestamp: str) -> dict[str, str]:
    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "open": "10.0",
        "high": "10.1",
        "low": "9.9",
        "close": "10.0",
        "volume": "1000",
        "trade_count": "100",
        "vwap": "10.0",
    }
