from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
from pathlib import Path

from tests.test_alpaca_scanner_quality import _write_rows, _write_session
from trading_agent.alpaca_scanner_quality import analyze_alpaca_scanner_quality
from trading_agent.alpaca_scanner_quality_models import ScannerQualityConfig, ScannerQualityOutcome

CONFIG = ScannerQualityConfig(0.02, 0.5, 100.0, 250_000.0, 0.01)


def test_scanner_quality_censors_path_without_entry_minute_bar(tmp_path: Path) -> None:
    # Given
    _write_session(tmp_path)
    _remove_bar(tmp_path, dt.time(9, 31))

    # When
    outcome = _base_outcome(tmp_path)

    # Then
    assert not outcome.complete
    assert outcome.entry is None


def test_scanner_quality_censors_path_without_exit_minute_bar(tmp_path: Path) -> None:
    # Given
    _write_session(tmp_path)
    _remove_bar(tmp_path, dt.time(15, 59))

    # When
    outcome = _base_outcome(tmp_path)

    # Then
    assert not outcome.complete
    assert outcome.eod_return is None


def test_scanner_quality_uses_wall_clock_horizons_when_no_eligible_trade_bar_exists(
    tmp_path: Path,
) -> None:
    # Given
    _write_session(tmp_path)
    _remove_bar(tmp_path, dt.time(9, 35), reprice_at=dt.time(9, 36))

    # When
    outcome = _base_outcome(tmp_path)

    # Then
    assert outcome.complete
    assert outcome.bar_count == 388
    assert outcome.return_5m == 0.0
    assert outcome.eod_return == 0.02


def _base_outcome(root: Path) -> ScannerQualityOutcome:
    return next(row for row in analyze_alpaca_scanner_quality(root, (CONFIG,)) if row.symbol == "BASE")


def _remove_bar(root: Path, missing_at: dt.time, *, reprice_at: dt.time | None = None) -> None:
    date_path = Path("2026/06/12")
    bars_path = root / "candidate_minutes" / date_path / "archive_demo" / "batch_00000.csv.gz"
    with gzip.open(bars_path, "rt", encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    retained: list[dict[str, str]] = []
    for row in rows:
        timestamp = dt.datetime.fromisoformat(row["timestamp"])
        if timestamp.time() == missing_at:
            continue
        if reprice_at is not None and timestamp.time() == reprice_at:
            row = {**row, "high": "11.0", "close": "11.0"}
        retained.append(row)
    _write_rows(bars_path, tuple(retained))
    for metadata_path in (
        root / "candidate_minutes" / date_path / "archive_demo" / "session.metadata.json",
        root / "staged_sessions" / date_path / "session_demo.metadata.json",
    ):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["bar_count" if "archive_demo" in metadata_path.parts else "candidate_bar_count"] = len(retained)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
