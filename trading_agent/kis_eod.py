from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from trading_agent.kis_provider import KisRankedStock
from trading_agent.kis_scan import KisPaperScanner, ScanObservation


@dataclass(frozen=True, slots=True)
class EodCatchupRun:
    session_date: dt.date
    observed_at: dt.datetime
    observations: tuple[ScanObservation, ...]

    @property
    def candidate_count(self) -> int:
        return len(self.observations)

    @property
    def complete_count(self) -> int:
        return sum(not row.status.startswith("오류:") for row in self.observations)

    @property
    def failure_count(self) -> int:
        return self.candidate_count - self.complete_count


def catch_up_candidates(
    scanner: KisPaperScanner,
    candidates: tuple[KisRankedStock, ...],
    max_pages: int,
    session_date: dt.date,
    observed_at: dt.datetime,
) -> EodCatchupRun:
    return EodCatchupRun(
        session_date,
        observed_at,
        tuple(
            scanner.catch_up_after_close(
                stock,
                max_pages=max_pages,
                session_date=session_date,
                now=observed_at,
            )
            for stock in candidates
        ),
    )


def duplicate_symbols(candidates: tuple[KisRankedStock, ...]) -> tuple[str, ...]:
    exchanges: dict[str, set[str]] = {}
    for row in candidates:
        exchanges.setdefault(row.symbol, set()).add(row.exchange)
    return tuple(sorted(symbol for symbol, values in exchanges.items() if len(values) > 1))


def append_eod_artifacts(output: Path, result: EodCatchupRun) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _append_row(
        output / "kis_eod_catchup_summary.csv",
        (
            "session_date",
            "observed_at",
            "candidate_count",
            "complete_count",
            "failure_count",
        ),
        (
            result.session_date.isoformat(),
            result.observed_at.isoformat(),
            result.candidate_count,
            result.complete_count,
            result.failure_count,
        ),
    )
    for row in result.observations:
        _append_row(
            output / "kis_eod_catchup_observations.csv",
            ("observed_at", "exchange", "symbol", "bars", "complete", "status"),
            (
                result.observed_at.isoformat(),
                row.exchange,
                row.symbol,
                row.bars,
                not row.status.startswith("오류:"),
                row.status,
            ),
        )


def _append_row(
    path: Path,
    header: tuple[str, ...],
    row: tuple[str | int | bool, ...],
) -> None:
    has_header = path.is_file() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not has_header:
            writer.writerow(header)
        writer.writerow(row)
