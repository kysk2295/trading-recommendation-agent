from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override
from zoneinfo import ZoneInfo

from trading_agent.forward_premarket_inputs import (
    PremarketCoverageRow,
    PremarketInputError,
    PremarketRiskRow,
    PremarketSnapshotRow,
    load_premarket_inputs,
)
from trading_agent.kis_live import premarket_session_is_open
from trading_agent.ranking_journal import RankingSource

_NEW_YORK: Final = ZoneInfo("America/New_York")
_EXCHANGES: Final = ("NAS", "NYS", "AMS")
_SOURCES: Final = tuple(RankingSource)


@dataclass(frozen=True, slots=True)
class PremarketReadiness:
    ready: bool
    session_date: dt.date
    input_sha256: str
    premarket_cycles: int
    ranking_requests: int
    ranking_snapshot_rows: int
    latest_observed_at: dt.datetime | None
    latest_age_seconds: int | None
    latest_selected_candidates: int
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PremarketReadinessError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


def audit_forward_premarket_readiness(
    session: Path,
    session_date: dt.date,
    minimum_cycles: int,
    maximum_latest_age_seconds: int,
    minimum_latest_selected: int,
    observed_at: dt.datetime,
) -> PremarketReadiness:
    if (
        not 1 <= minimum_cycles <= 65
        or not 60 <= maximum_latest_age_seconds <= 3_600
        or not 1 <= minimum_latest_selected <= 10
        or not _aware(observed_at)
    ):
        raise PremarketReadinessError("invalid_readiness_request")
    try:
        source = load_premarket_inputs(session)
    except PremarketInputError as error:
        raise PremarketReadinessError(error.reason) from None
    watch = source.watch
    coverage = source.coverage
    snapshots = source.snapshots
    risks = source.risks
    blockers: list[str] = []
    current = observed_at.astimezone(_NEW_YORK)
    if current.date() != session_date:
        blockers.append(
            f"session_date_mismatch:{current.date().isoformat()}/{session_date.isoformat()}"
        )
    if not premarket_session_is_open(current):
        blockers.append("current_premarket_closed")
    if len(watch) < minimum_cycles:
        blockers.append(f"minimum_cycles_unmet:{len(watch)}/{minimum_cycles}")
    failed_watch = sum(
        row.exit_code != 0 or row.status != "ok" for row in watch
    )
    if failed_watch:
        blockers.append(f"watch_cycle_failures:{failed_watch}")
    coverage_by_time = _coverage_by_time(coverage)
    if len(coverage_by_time) != len(watch):
        blockers.append(
            f"coverage_cycle_mismatch:{len(coverage_by_time)}/{len(watch)}"
        )
    failed_requests = sum(row.status == "failed" for row in coverage)
    if failed_requests:
        blockers.append(f"ranking_request_failures:{failed_requests}")
    expected_keys = {
        (source, exchange)
        for exchange in _EXCHANGES
        for source in _SOURCES
    }
    for rows in coverage_by_time.values():
        keys = {(row.ranking_source, row.exchange) for row in rows}
        if len(rows) != len(expected_keys) or keys != expected_keys:
            blockers.append("ranking_request_set_mismatch")
            break
    snapshot_counts = _snapshot_counts(snapshots)
    expected_snapshot_counts = {
        (row.observed_at, row.ranking_source, row.exchange): _row_count(row)
        for row in coverage
        if row.status == "ok"
    }
    if snapshot_counts != expected_snapshot_counts:
        blockers.append("ranking_snapshot_count_mismatch")
    cycle_times = tuple(sorted(coverage_by_time))
    if len(watch) == len(cycle_times) and any(
        not 0
        <= (cycle_time - watch_row.started_at).total_seconds()
        <= 180
        for watch_row, cycle_time in zip(watch, cycle_times, strict=True)
    ):
        blockers.append("watch_coverage_time_mismatch")
    selection_by_time = _snapshot_selections(snapshots)
    risk_selection_by_time, duplicate_risk_rows = _risk_selections(risks)
    if duplicate_risk_rows:
        blockers.append(f"risk_duplicate_rows:{duplicate_risk_rows}")
    if set(risk_selection_by_time) != set(cycle_times):
        blockers.append("risk_cycle_mismatch")
    if any(
        selection_by_time.get(cycle_time, set())
        != risk_selection_by_time.get(cycle_time, set())
        for cycle_time in cycle_times
    ):
        blockers.append("selected_candidate_identity_mismatch")
    latest = cycle_times[-1] if cycle_times else None
    latest_age = (
        None
        if latest is None
        else int((observed_at - latest).total_seconds())
    )
    if latest is None:
        blockers.append("premarket_cycles_empty")
    elif latest.astimezone(_NEW_YORK).date() != session_date:
        blockers.append("latest_session_date_mismatch")
    elif latest_age is None or not 0 <= latest_age <= maximum_latest_age_seconds:
        blockers.append(
            f"latest_cycle_stale:{latest_age}/{maximum_latest_age_seconds}"
        )
    latest_selected = (
        0
        if latest is None
        else len(selection_by_time.get(latest, set()))
    )
    if latest_selected < minimum_latest_selected:
        blockers.append(
            f"latest_selected_unmet:{latest_selected}/{minimum_latest_selected}"
        )
    return PremarketReadiness(
        ready=not blockers,
        session_date=session_date,
        input_sha256=source.input_sha256,
        premarket_cycles=len(watch),
        ranking_requests=len(coverage),
        ranking_snapshot_rows=len(snapshots),
        latest_observed_at=latest,
        latest_age_seconds=latest_age,
        latest_selected_candidates=latest_selected,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def _coverage_by_time(
    rows: tuple[PremarketCoverageRow, ...],
) -> dict[dt.datetime, list[PremarketCoverageRow]]:
    grouped: dict[dt.datetime, list[PremarketCoverageRow]] = {}
    for row in rows:
        grouped.setdefault(row.observed_at, []).append(row)
    return grouped


def _snapshot_counts(
    rows: tuple[PremarketSnapshotRow, ...],
) -> dict[tuple[dt.datetime, RankingSource, str], int]:
    counts: dict[tuple[dt.datetime, RankingSource, str], int] = {}
    for row in rows:
        key = (row.observed_at, row.ranking_source, row.exchange)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _snapshot_selections(
    rows: tuple[PremarketSnapshotRow, ...],
) -> dict[dt.datetime, set[tuple[str, str]]]:
    selected: dict[dt.datetime, set[tuple[str, str]]] = {}
    for row in rows:
        selected.setdefault(row.observed_at, set())
        if row.selection_input:
            selected[row.observed_at].add((row.exchange, row.symbol))
    return selected


def _risk_selections(
    rows: tuple[PremarketRiskRow, ...],
) -> tuple[dict[dt.datetime, set[tuple[str, str]]], int]:
    selected: dict[dt.datetime, set[tuple[str, str]]] = {}
    identities: set[tuple[dt.datetime, str, str]] = set()
    duplicates = 0
    for row in rows:
        key = (row.observed_at, row.exchange, row.symbol)
        duplicates += key in identities
        identities.add(key)
        selected.setdefault(row.observed_at, set())
        if row.selected:
            selected[row.observed_at].add((row.exchange, row.symbol))
    return selected, duplicates


def _row_count(row: PremarketCoverageRow) -> int:
    try:
        value = int(row.row_count)
    except ValueError:
        return -1
    return value if value > 0 and not row.reason else -1


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "PremarketReadiness",
    "PremarketReadinessError",
    "audit_forward_premarket_readiness",
)
