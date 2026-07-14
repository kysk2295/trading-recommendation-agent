from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CandidateInputCycleAudit:
    started_at: dt.datetime
    selected_count: int
    context_count: int
    scan_completed: bool


def append_candidate_input_cycle(
    path: Path,
    audit: CandidateInputCycleAudit,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    has_header = path.is_file() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not has_header:
            writer.writerow(
                (
                    "started_at",
                    "selected_count",
                    "context_count",
                    "scan_completed",
                )
            )
        writer.writerow(
            (
                audit.started_at.isoformat(),
                audit.selected_count,
                audit.context_count,
                audit.scan_completed,
            )
        )
