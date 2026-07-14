from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from trading_agent.kis_scan import ScanObservation
from trading_agent.scan_cycle import (
    CycleRuntime,
    WatchConfig,
    append_cycle_audit,
    run_cycles,
    scan_exit_code,
)


def test_cycle_runner_continues_after_a_failed_scan() -> None:
    pending = iter((1, 0))
    waits: list[float] = []
    config = WatchConfig(cycles=2, interval_seconds=60.0)

    exit_codes = run_cycles(
        lambda: next(pending),
        config,
        CycleRuntime(waits.append),
    )

    assert exit_codes == (1, 0)
    assert waits == [60.0]


def test_cycle_runner_stops_before_an_operation_when_session_closes() -> None:
    session_checks = iter((True, True, False))
    operations: list[int] = []
    waits: list[float] = []
    config = WatchConfig(cycles=4, interval_seconds=60.0)

    exit_codes = run_cycles(
        lambda: operations.append(0) or 0,
        config,
        CycleRuntime(waits.append, lambda: next(session_checks)),
    )

    assert exit_codes == (0, 0)
    assert operations == [0, 0]
    assert waits == [60.0, 60.0]


def test_cycle_audit_appends_failure_and_recovery(tmp_path: Path) -> None:
    path = tmp_path / "watch_cycles.csv"
    started_at = dt.datetime(2026, 7, 10, 9, 30, tzinfo=dt.UTC)

    append_cycle_audit(path, started_at, 1)
    append_cycle_audit(
        path,
        started_at + dt.timedelta(minutes=1),
        0,
    )

    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    assert tuple(row["status"] for row in rows) == ("failed", "ok")


def test_partial_provider_error_makes_the_scan_cycle_fail() -> None:
    observations = (
        ScanObservation("NAS", "AAA", 0.1, 10.0, 20.0, 3, "최신 완료 봉 평가"),
        ScanObservation("NYS", "BBB", 0.2, 5.0, 30.0, 0, "오류: HTTP 500"),
    )

    exit_code = scan_exit_code(observations)

    assert exit_code == 1


def test_opening_gap_provider_error_makes_the_scan_cycle_fail() -> None:
    observations = (
        ScanObservation("NAS", "AAA", 0.1, 10.0, 20.0, 3, "최신 완료 봉 평가"),
    )

    exit_code = scan_exit_code(
        observations,
        opening_gap_failure_count=1,
    )

    assert exit_code == 1
