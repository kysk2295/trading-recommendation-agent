from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from scr_backtest.kis_http import KisReadRetryEvent
from trading_agent.kis_retry_audit import append_kis_retry_audit


def test_retry_audit_writes_cycle_summary_and_safe_event_details(
    tmp_path: Path,
) -> None:
    started_at = dt.datetime(2026, 7, 14, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    events = (
        KisReadRetryEvent("/minute", "NAS", "GOOD", 500, 200, "recovered"),
        KisReadRetryEvent("/daily", "AMS", "BAD", 503, 503, "failed"),
    )

    append_kis_retry_audit(tmp_path, started_at, events)

    with (tmp_path / "kis_read_retry_cycles.csv").open(encoding="utf-8", newline="") as handle:
        cycles = tuple(csv.DictReader(handle))
    with (tmp_path / "kis_read_retry_events.csv").open(encoding="utf-8", newline="") as handle:
        details = tuple(csv.DictReader(handle))
    assert cycles == (
        {
            "started_at": started_at.isoformat(),
            "retry_count": "2",
            "recovered_count": "1",
            "repeated_failure_count": "1",
        },
    )
    assert tuple(row["symbol"] for row in details) == ("GOOD", "BAD")
    assert tuple(row["outcome"] for row in details) == ("recovered", "failed")
    assert all("authorization" not in value for row in details for value in row.values())


def test_retry_audit_can_isolate_eod_reads_from_regular_watch_cycles(tmp_path: Path) -> None:
    started_at = dt.datetime(2026, 7, 14, 16, 1, tzinfo=ZoneInfo("America/New_York"))

    append_kis_retry_audit(tmp_path, started_at, (), artifact_prefix="eod_kis_read_retry")

    assert (tmp_path / "eod_kis_read_retry_cycles.csv").is_file()
    assert not (tmp_path / "kis_read_retry_cycles.csv").exists()
