from __future__ import annotations

import csv
import datetime as dt
import statistics
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from trading_agent.market_risk import PORTFOLIO_LIMIT_REASON
from trading_agent.session_continuity import RiskScreenRow, canonical_exchange


@dataclass(frozen=True, slots=True)
class CandidatePersistence:
    canonical_exchange: str
    symbol: str
    first_observed_at: dt.datetime
    last_observed_at: dt.datetime
    observed_snapshot_count: int
    eligible_snapshot_count: int
    selected_snapshot_count: int
    maximum_change_pct: float


@dataclass(frozen=True, slots=True)
class SnapshotTransition:
    source_observed_at: dt.datetime
    destination_observed_at: dt.datetime
    source_eligible_count: int
    destination_eligible_count: int
    continued_count: int
    continuation_rate: float | None
    jaccard: float | None


@dataclass(frozen=True, slots=True)
class PersistenceSummary:
    snapshot_count: int
    candidate_count: int
    transition_count: int
    risk_eligible_occurrences: int
    selected_occurrences: int
    mean_continuation_rate: float | None
    mean_jaccard: float | None


@dataclass(frozen=True, slots=True)
class PersistenceResult:
    candidates: tuple[CandidatePersistence, ...]
    transitions: tuple[SnapshotTransition, ...]
    summary: PersistenceSummary


def analyze_candidate_persistence(path: Path) -> PersistenceResult:
    rows = _read_rows(path)
    snapshots: dict[dt.datetime, list[RiskScreenRow]] = {}
    candidates: dict[tuple[str, str], list[RiskScreenRow]] = {}
    for row in rows:
        snapshots.setdefault(row.observed_at, []).append(row)
        key = (canonical_exchange(row.exchange), row.symbol)
        candidates.setdefault(key, []).append(row)
    measured_candidates = tuple(
        _measure_candidate(exchange, symbol, candidate_rows)
        for (exchange, symbol), candidate_rows in sorted(candidates.items())
    )
    observed_at = sorted(snapshots)
    transitions = tuple(
        _measure_transition(
            source,
            destination,
            snapshots[source],
            snapshots[destination],
        )
        for source, destination in pairwise(observed_at)
    )
    continuation_rates = tuple(
        row.continuation_rate
        for row in transitions
        if row.continuation_rate is not None
    )
    jaccards = tuple(row.jaccard for row in transitions if row.jaccard is not None)
    summary = PersistenceSummary(
        len(snapshots),
        len(measured_candidates),
        len(transitions),
        sum(_is_eligible(row) for row in rows),
        sum(row.selected for row in rows),
        statistics.fmean(continuation_rates) if continuation_rates else None,
        statistics.fmean(jaccards) if jaccards else None,
    )
    return PersistenceResult(measured_candidates, transitions, summary)


def write_candidate_persistence(output: Path, result: PersistenceResult) -> None:
    from trading_agent.candidate_persistence_report import write_candidate_persistence

    write_candidate_persistence(output, result)


def _read_rows(path: Path) -> tuple[RiskScreenRow, ...]:
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(RiskScreenRow.model_validate(row) for row in csv.DictReader(handle))


def _measure_candidate(
    exchange: str,
    symbol: str,
    rows: list[RiskScreenRow],
) -> CandidatePersistence:
    observed = {row.observed_at for row in rows}
    eligible = {row.observed_at for row in rows if _is_eligible(row)}
    selected = {row.observed_at for row in rows if row.selected}
    return CandidatePersistence(
        exchange,
        symbol,
        min(observed),
        max(observed),
        len(observed),
        len(eligible),
        len(selected),
        max(row.change_pct for row in rows),
    )


def _measure_transition(
    source_at: dt.datetime,
    destination_at: dt.datetime,
    source_rows: list[RiskScreenRow],
    destination_rows: list[RiskScreenRow],
) -> SnapshotTransition:
    source = _eligible_keys(source_rows)
    destination = _eligible_keys(destination_rows)
    continued = source & destination
    union = source | destination
    return SnapshotTransition(
        source_at,
        destination_at,
        len(source),
        len(destination),
        len(continued),
        None if not source else len(continued) / len(source),
        None if not union else len(continued) / len(union),
    )


def _eligible_keys(rows: list[RiskScreenRow]) -> set[tuple[str, str]]:
    return {
        (canonical_exchange(row.exchange), row.symbol)
        for row in rows
        if _is_eligible(row)
    }


def _is_eligible(row: RiskScreenRow) -> bool:
    return row.reason in ("", PORTFOLIO_LIMIT_REASON)
