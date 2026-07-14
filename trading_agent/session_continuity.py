from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from pydantic import BaseModel, ConfigDict

from trading_agent.market_risk import PORTFOLIO_LIMIT_REASON


class MarketPhase(StrEnum):
    DAYTIME = "daytime"
    PREMARKET = "premarket"
    REGULAR = "regular"


class SupportedExchange(StrEnum):
    BAQ = "BAQ"
    BAY = "BAY"
    BAA = "BAA"
    NAS = "NAS"
    NYS = "NYS"
    AMS = "AMS"


class RiskScreenRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: dt.datetime
    exchange: SupportedExchange
    symbol: str
    selected: bool
    reason: str
    change_pct: float


@dataclass(frozen=True, slots=True)
class SessionFiles:
    daytime: Path | None
    premarket: Path | None
    regular: Path | None


@dataclass(frozen=True, slots=True)
class PhaseStats:
    first_observed_at: dt.datetime
    risk_eligible: bool
    selected: bool
    maximum_change_pct: float


@dataclass(frozen=True, slots=True)
class CandidateContinuity:
    canonical_exchange: str
    symbol: str
    daytime: PhaseStats | None
    premarket: PhaseStats | None
    regular: PhaseStats | None
    daytime_to_premarket: bool
    premarket_to_regular: bool
    daytime_to_regular: bool


@dataclass(frozen=True, slots=True)
class TransitionSummary:
    source_phase: MarketPhase
    destination_phase: MarketPhase
    source_eligible_count: int
    destination_eligible_count: int
    continued_count: int
    continuation_rate: float | None


@dataclass(frozen=True, slots=True)
class ContinuityResult:
    candidates: tuple[CandidateContinuity, ...]
    summaries: tuple[TransitionSummary, ...]


@dataclass(frozen=True, slots=True)
class PhaseRiskRow:
    phase: MarketPhase
    row: RiskScreenRow


def analyze_session_continuity(files: SessionFiles) -> ContinuityResult:
    rows = tuple(
        row
        for phase, path in (
            (MarketPhase.DAYTIME, files.daytime),
            (MarketPhase.PREMARKET, files.premarket),
            (MarketPhase.REGULAR, files.regular),
        )
        for row in _read_phase(path, phase)
    )
    observations: dict[tuple[str, str, MarketPhase], PhaseStats] = {}
    for phase_row in rows:
        row = phase_row.row
        key = (canonical_exchange(row.exchange), row.symbol, phase_row.phase)
        eligible = row.reason in ("", PORTFOLIO_LIMIT_REASON)
        current = observations.get(key)
        observations[key] = PhaseStats(
            first_observed_at=(
                row.observed_at
                if current is None
                else min(current.first_observed_at, row.observed_at)
            ),
            risk_eligible=eligible or (current is not None and current.risk_eligible),
            selected=row.selected or (current is not None and current.selected),
            maximum_change_pct=(
                row.change_pct
                if current is None
                else max(current.maximum_change_pct, row.change_pct)
            ),
        )
    candidate_keys = sorted({(exchange, symbol) for exchange, symbol, _ in observations})
    candidates = tuple(
        _candidate_continuity(exchange, symbol, observations)
        for exchange, symbol in candidate_keys
    )
    summaries = tuple(
        _transition_summary(candidates, source, destination)
        for source, destination in (
            (MarketPhase.DAYTIME, MarketPhase.PREMARKET),
            (MarketPhase.PREMARKET, MarketPhase.REGULAR),
            (MarketPhase.DAYTIME, MarketPhase.REGULAR),
        )
    )
    return ContinuityResult(candidates, summaries)


def write_continuity_outputs(output: Path, result: ContinuityResult) -> None:
    from trading_agent.session_continuity_report import (
        write_continuity_outputs as write_outputs,
    )

    write_outputs(output, result)


def _read_phase(path: Path | None, phase: MarketPhase) -> tuple[PhaseRiskRow, ...]:
    if path is None or not path.is_file():
        return ()
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(
            PhaseRiskRow(phase, RiskScreenRow.model_validate(row))
            for row in csv.DictReader(handle)
        )


def canonical_exchange(exchange: SupportedExchange) -> str:
    match exchange:
        case SupportedExchange.BAQ | SupportedExchange.NAS:
            return SupportedExchange.NAS.value
        case SupportedExchange.BAY | SupportedExchange.NYS:
            return SupportedExchange.NYS.value
        case SupportedExchange.BAA | SupportedExchange.AMS:
            return SupportedExchange.AMS.value
        case unreachable:
            assert_never(unreachable)


def _candidate_continuity(
    exchange: str,
    symbol: str,
    observations: dict[tuple[str, str, MarketPhase], PhaseStats],
) -> CandidateContinuity:
    daytime = observations.get((exchange, symbol, MarketPhase.DAYTIME))
    premarket = observations.get((exchange, symbol, MarketPhase.PREMARKET))
    regular = observations.get((exchange, symbol, MarketPhase.REGULAR))
    return CandidateContinuity(
        exchange,
        symbol,
        daytime,
        premarket,
        regular,
        _continues(daytime, premarket),
        _continues(premarket, regular),
        _continues(daytime, regular),
    )


def _continues(source: PhaseStats | None, destination: PhaseStats | None) -> bool:
    return (
        source is not None
        and destination is not None
        and source.risk_eligible
        and destination.risk_eligible
    )


def _transition_summary(
    candidates: tuple[CandidateContinuity, ...],
    source: MarketPhase,
    destination: MarketPhase,
) -> TransitionSummary:
    source_count = sum(_eligible(candidate, source) for candidate in candidates)
    destination_count = sum(
        _eligible(candidate, destination) for candidate in candidates
    )
    continued_count = sum(
        _continues(_phase_stats(candidate, source), _phase_stats(candidate, destination))
        for candidate in candidates
    )
    destination_observed = any(
        _phase_stats(candidate, destination) is not None for candidate in candidates
    )
    return TransitionSummary(
        source,
        destination,
        source_count,
        destination_count,
        continued_count,
        (
            None
            if source_count == 0 or not destination_observed
            else continued_count / source_count
        ),
    )


def _eligible(candidate: CandidateContinuity, phase: MarketPhase) -> bool:
    stats = _phase_stats(candidate, phase)
    return stats is not None and stats.risk_eligible


def _phase_stats(
    candidate: CandidateContinuity,
    phase: MarketPhase,
) -> PhaseStats | None:
    match phase:
        case MarketPhase.DAYTIME:
            return candidate.daytime
        case MarketPhase.PREMARKET:
            return candidate.premarket
        case MarketPhase.REGULAR:
            return candidate.regular
        case unreachable:
            assert_never(unreachable)
