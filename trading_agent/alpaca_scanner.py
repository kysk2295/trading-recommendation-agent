from __future__ import annotations

import csv
import datetime as dt
import gzip
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from trading_agent.alpaca_reference import AlpacaDailyReference

NEW_YORK: Final = ZoneInfo("America/New_York")
DECISION_HEADER: Final = (
    "symbol",
    "selected",
    "rank",
    "reason",
    "last_timestamp",
    "price",
    "known_gap_pct",
    "change_pct",
    "observed_volume",
    "dollar_volume",
    "adv_fraction",
    "prior_close",
    "average_volume",
    "history_sessions",
)


@dataclass(frozen=True, slots=True)
class AlpacaScannerConfig:
    min_change_pct: float = 0.02
    min_price: float = 0.50
    max_price: float = 100.0
    min_dollar_volume: float = 250_000.0
    min_adv_fraction: float = 0.01
    max_candidates: int = 200

    def __post_init__(self) -> None:
        if self.min_price <= 0 or self.max_price < self.min_price:
            raise ValueError("스캐너 가격 범위가 올바르지 않습니다")
        if self.min_dollar_volume < 0 or self.min_adv_fraction < 0:
            raise ValueError("스캐너 유동성 기준은 음수일 수 없습니다")
        if self.max_candidates <= 0:
            raise ValueError("스캐너 후보 상한은 1개 이상이어야 합니다")


@dataclass(frozen=True, slots=True)
class AlpacaScannerDecision:
    symbol: str
    selected: bool
    rank: int | None
    reason: str
    last_timestamp: dt.datetime | None
    price: float | None
    known_gap_pct: float | None
    change_pct: float | None
    observed_volume: int
    dollar_volume: float
    adv_fraction: float | None
    prior_close: float | None
    average_volume: float | None
    history_sessions: int


@dataclass(slots=True)
class _ObservedState:
    last_timestamp: dt.datetime | None = None
    last_close: float | None = None
    regular_open: float | None = None
    volume: int = 0
    dollar_volume: float = 0.0


def scan_alpaca_archive(
    archive_dir: Path,
    session_date: dt.date,
    cutoff: dt.time,
    symbols: tuple[str, ...],
    references: tuple[AlpacaDailyReference, ...],
    config: AlpacaScannerConfig,
) -> tuple[AlpacaScannerDecision, ...]:
    states = _load_states(archive_dir, session_date, cutoff)
    reference_by_symbol = {reference.symbol: reference for reference in references}
    decisions = [
        _decision(symbol, states.get(symbol), reference_by_symbol.get(symbol), config)
        for symbol in sorted(set(symbols))
    ]
    eligible = sorted(
        (decision for decision in decisions if decision.reason == "eligible"),
        key=lambda item: (
            -(item.change_pct or 0.0),
            -(item.adv_fraction or 0.0),
            -item.dollar_volume,
            item.symbol,
        ),
    )
    selected_ranks = {decision.symbol: rank for rank, decision in enumerate(eligible[: config.max_candidates], 1)}
    return tuple(
        replace(
            decision,
            selected=decision.symbol in selected_ranks,
            rank=selected_ranks.get(decision.symbol),
            reason="selected" if decision.symbol in selected_ranks else _final_reason(decision),
        )
        for decision in decisions
    )


def write_scanner_decisions(path: Path, decisions: tuple[AlpacaScannerDecision, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".gz.part")
    with gzip.open(temporary, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(DECISION_HEADER)
        for decision in decisions:
            writer.writerow(
                (
                    decision.symbol,
                    decision.selected,
                    "" if decision.rank is None else decision.rank,
                    decision.reason,
                    "" if decision.last_timestamp is None else decision.last_timestamp.isoformat(),
                    _optional(decision.price),
                    _optional(decision.known_gap_pct),
                    _optional(decision.change_pct),
                    decision.observed_volume,
                    decision.dollar_volume,
                    _optional(decision.adv_fraction),
                    _optional(decision.prior_close),
                    _optional(decision.average_volume),
                    decision.history_sessions,
                )
            )
    temporary.replace(path)


def _load_states(archive_dir: Path, session_date: dt.date, cutoff: dt.time) -> dict[str, _ObservedState]:
    states: dict[str, _ObservedState] = {}
    cutoff_at = dt.datetime.combine(session_date, cutoff, tzinfo=NEW_YORK)
    regular_open_at = dt.datetime.combine(session_date, dt.time(9, 30), tzinfo=NEW_YORK)
    for path in sorted(archive_dir.glob("batch_*.csv.gz")):
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                timestamp = dt.datetime.fromisoformat(row["timestamp"]).astimezone(NEW_YORK)
                if timestamp >= cutoff_at:
                    continue
                state = states.setdefault(row["symbol"], _ObservedState())
                volume = int(row["volume"])
                close = float(row["close"])
                vwap = float(row["vwap"]) if row["vwap"] else close
                state.volume += volume
                state.dollar_volume += volume * vwap
                if state.last_timestamp is None or timestamp > state.last_timestamp:
                    state.last_timestamp = timestamp
                    state.last_close = close
                if timestamp >= regular_open_at and state.regular_open is None:
                    state.regular_open = float(row["open"])
    return states


def _decision(
    symbol: str,
    state: _ObservedState | None,
    reference: AlpacaDailyReference | None,
    config: AlpacaScannerConfig,
) -> AlpacaScannerDecision:
    prior_close = None if reference is None else reference.prior_close
    average_volume = None if reference is None else reference.average_volume
    history_sessions = 0 if reference is None else reference.history_sessions
    price = None if state is None else state.last_close
    gap_price = price if state is None or state.regular_open is None else state.regular_open
    change_pct = _return(price, prior_close)
    known_gap_pct = _return(gap_price, prior_close)
    adv_fraction = None if state is None or not average_volume else state.volume / average_volume
    reason = _rejection_reason(state, price, change_pct, adv_fraction, prior_close, average_volume, config)
    return AlpacaScannerDecision(
        symbol=symbol,
        selected=False,
        rank=None,
        reason=reason,
        last_timestamp=None if state is None else state.last_timestamp,
        price=price,
        known_gap_pct=known_gap_pct,
        change_pct=change_pct,
        observed_volume=0 if state is None else state.volume,
        dollar_volume=0.0 if state is None else state.dollar_volume,
        adv_fraction=adv_fraction,
        prior_close=prior_close,
        average_volume=average_volume,
        history_sessions=history_sessions,
    )


def _rejection_reason(
    state: _ObservedState | None,
    price: float | None,
    change_pct: float | None,
    adv_fraction: float | None,
    prior_close: float | None,
    average_volume: float | None,
    config: AlpacaScannerConfig,
) -> str:
    if prior_close is None or average_volume is None:
        return "missing_daily_reference"
    if state is None or price is None:
        return "no_scanner_bar"
    if price < config.min_price or price > config.max_price:
        return "price"
    if change_pct is None or change_pct < config.min_change_pct:
        return "change"
    if state.dollar_volume < config.min_dollar_volume:
        return "dollar_volume"
    if adv_fraction is None or adv_fraction < config.min_adv_fraction:
        return "adv_fraction"
    return "eligible"


def _final_reason(decision: AlpacaScannerDecision) -> str:
    return "candidate_cap" if decision.reason == "eligible" else decision.reason


def _return(price: float | None, prior_close: float | None) -> float | None:
    if price is None or prior_close is None or prior_close <= 0:
        return None
    return price / prior_close - 1.0


def _optional(value: float | None) -> float | str:
    return "" if value is None else value
