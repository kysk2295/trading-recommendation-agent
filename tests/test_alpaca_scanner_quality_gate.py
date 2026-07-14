from __future__ import annotations

import datetime as dt

from trading_agent.alpaca_scanner_quality_gate import (
    ScannerQualityGateConfig,
    evaluate_scanner_quality_gate,
)
from trading_agent.alpaca_scanner_quality_models import (
    ScannerQualityConfig,
    ScannerQualityOutcome,
)


def test_scanner_quality_gate_counts_unique_candidate_days() -> None:
    config = ScannerQualityConfig(0.02, 0.5, 100.0, 250_000.0, 0.01)
    alternate = ScannerQualityConfig(0.04, 0.5, 100.0, 250_000.0, 0.01)
    date = dt.date(2026, 6, 12)
    entry_at = dt.datetime(2026, 6, 12, 9, 31, tzinfo=dt.UTC)
    outcomes = (
        ScannerQualityOutcome(config, date, "GOOD", 1, 389, True, entry_at, 10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        ScannerQualityOutcome(alternate, date, "GOOD", 1, 389, True, entry_at, 10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        ScannerQualityOutcome(config, date, "MISSING", 2, 0, False, entry_at, None, None, None, None, None, None, None),
    )

    gate = evaluate_scanner_quality_gate(
        outcomes,
        ScannerQualityGateConfig(minimum_path_coverage=0.8, minimum_complete_candidate_days=2),
    )

    assert not gate.passed
    assert gate.unique_candidate_days == 2
    assert gate.complete_candidate_days == 1
    assert gate.path_coverage == 0.5
    assert gate.issues == ("path_coverage:0.500000<0.800000", "complete_candidate_days:1<2")
