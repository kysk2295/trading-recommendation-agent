from __future__ import annotations

import csv
import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from trading_agent.kis_scan import ScanObservation
from trading_agent.private_report import open_private_append


@dataclass(frozen=True, slots=True)
class WatchConfig:
    cycles: int
    interval_seconds: float


@dataclass(frozen=True, slots=True)
class CycleRuntime:
    sleeper: Callable[[float], None]
    should_continue: Callable[[], bool] | None = None
    monotonic: Callable[[], float] = time.monotonic


def run_cycles(
    operation: Callable[[], int],
    config: WatchConfig,
    runtime: CycleRuntime,
) -> tuple[int, ...]:
    exit_codes: list[int] = []
    for cycle in range(config.cycles):
        if runtime.should_continue is not None and not runtime.should_continue():
            break
        started_at = runtime.monotonic()
        exit_codes.append(operation())
        if cycle + 1 < config.cycles:
            operation_seconds = runtime.monotonic() - started_at
            runtime.sleeper(max(0.0, config.interval_seconds - operation_seconds))
    return tuple(exit_codes)


def append_cycle_audit(
    path: Path,
    started_at: dt.datetime,
    exit_code: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    has_header = path.is_file() and path.stat().st_size > 0
    with open_private_append(path) as handle:
        writer = csv.writer(handle)
        if not has_header:
            writer.writerow(("started_at", "exit_code", "status"))
        writer.writerow(
            (
                started_at.isoformat(),
                exit_code,
                "ok" if exit_code == 0 else "failed",
            )
        )


def scan_exit_code(
    observations: tuple[ScanObservation, ...],
    opening_gap_failure_count: int = 0,
    ranking_failure_count: int = 0,
) -> int:
    return int(
        opening_gap_failure_count > 0
        or ranking_failure_count > 0
        or any(row.status.startswith("오류:") for row in observations)
    )
