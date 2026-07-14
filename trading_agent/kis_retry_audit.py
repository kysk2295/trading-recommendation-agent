from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from scr_backtest.kis_http import RetryEvents


def append_kis_retry_audit(
    output: Path,
    started_at: dt.datetime,
    events: RetryEvents,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    recovered = sum(event.outcome == "recovered" for event in events)
    _append_row(
        output / "kis_read_retry_cycles.csv",
        (
            "started_at",
            "retry_count",
            "recovered_count",
            "repeated_failure_count",
        ),
        (
            started_at.isoformat(),
            len(events),
            recovered,
            len(events) - recovered,
        ),
    )
    for event in events:
        _append_row(
            output / "kis_read_retry_events.csv",
            (
                "started_at",
                "endpoint",
                "exchange",
                "symbol",
                "first_status",
                "final_status",
                "outcome",
            ),
            (
                started_at.isoformat(),
                event.endpoint,
                event.exchange,
                event.symbol,
                event.first_status,
                event.final_status,
                event.outcome,
            ),
        )


def _append_row(
    path: Path,
    header: tuple[str, ...],
    row: tuple[str | int, ...],
) -> None:
    has_header = path.is_file() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not has_header:
            writer.writerow(header)
        writer.writerow(row)
