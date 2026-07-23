from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.daily_research_models import SessionQuality
from trading_agent.daily_research_sources import load_session_quality

REQUIRED_PROGRESS_ARTIFACTS: Final = (
    "kis_ranking_request_coverage.csv",
    "watch_cycles.csv",
    "kis_read_retry_cycles.csv",
    "candidate_input_cycles.csv",
    "paper_recommendations.sqlite3",
)
BLOCKING_INCIDENT_PREFIXES: Final = (
    "coverage_cycle_mismatch:",
    "ranking_request_count_mismatch:",
    "ranking_request_failures:",
    "watch_cycle_failures:",
    "retry_cycle_mismatch:",
    "kis_read_retry_failures:",
    "candidate_input_cycle_mismatch:",
    "candidate_input_incomplete_cycles:",
    "candidate_input_count_mismatch:",
)


@dataclass(frozen=True, slots=True)
class ForwardSessionProgressError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class ForwardSessionProgress:
    clean: bool
    blockers: tuple[str, ...]
    incidents: tuple[str, ...]
    quality: SessionQuality | None


def audit_forward_session_progress(
    session: Path,
    minimum_watch_cycles: int,
) -> ForwardSessionProgress:
    if not 1 <= minimum_watch_cycles <= 390:
        raise ForwardSessionProgressError("invalid_minimum_watch_cycles")
    missing = tuple(
        name
        for name in REQUIRED_PROGRESS_ARTIFACTS
        if not (session / name).is_file()
    )
    if missing:
        return ForwardSessionProgress(
            False,
            tuple(f"artifact_missing:{name}" for name in missing),
            (),
            None,
        )
    try:
        quality, incidents = load_session_quality(session, completed_trades=0)
    except (OSError, sqlite3.Error, ValueError):
        return ForwardSessionProgress(False, ("session_unreadable",), (), None)
    blockers = [
        incident
        for incident in incidents
        if incident.startswith(BLOCKING_INCIDENT_PREFIXES)
    ]
    if quality.watch_cycles == 0:
        blockers.append("watch_cycles_empty")
    if quality.watch_cycles < minimum_watch_cycles:
        blockers.append(
            f"minimum_watch_cycles_unmet:{quality.watch_cycles}/{minimum_watch_cycles}"
        )
    return ForwardSessionProgress(
        not blockers and quality.forward_day_eligible,
        tuple(blockers),
        incidents,
        quality,
    )


__all__ = (
    "ForwardSessionProgress",
    "ForwardSessionProgressError",
    "audit_forward_session_progress",
)
