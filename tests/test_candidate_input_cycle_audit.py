from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent.candidate_input_audit import (
    CandidateInputCycleAudit,
    append_candidate_input_cycle,
)


def test_candidate_input_cycle_audit_appends_structured_coverage(
    tmp_path: Path,
) -> None:
    started_at = dt.datetime(2026, 7, 14, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    audit = CandidateInputCycleAudit(started_at, 10, 8, True)

    append_candidate_input_cycle(tmp_path / "candidate_input_cycles.csv", audit)

    with (tmp_path / "candidate_input_cycles.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = tuple(csv.DictReader(handle))
    assert rows == (
        {
            "started_at": started_at.isoformat(),
            "selected_count": "10",
            "context_count": "8",
            "scan_completed": "True",
        },
    )
