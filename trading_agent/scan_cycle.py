from __future__ import annotations

import csv
import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from trading_agent.kis_scan import ScanObservation


@dataclass(frozen=True, slots=True)
class WatchConfig:
    cycles: int
    interval_seconds: float


@dataclass(frozen=True, slots=True)
class CycleRuntime:
    sleeper: Callable[[float], None]
    should_continue: Callable[[], bool] | None = None


def run_cycles(
    operation: Callable[[], int],
    config: WatchConfig,
    runtime: CycleRuntime,
) -> tuple[int, ...]:
    exit_codes: list[int] = []
    for cycle in range(config.cycles):
        if runtime.should_continue is not None and not runtime.should_continue():
            break
        exit_codes.append(operation())
        if cycle + 1 < config.cycles:
            runtime.sleeper(config.interval_seconds)
    return tuple(exit_codes)


def append_cycle_audit(
    path: Path,
    started_at: dt.datetime,
    exit_code: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    has_header = path.is_file() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
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
) -> int:
    return int(
        opening_gap_failure_count > 0
        or any(row.status.startswith("오류:") for row in observations)
    )
