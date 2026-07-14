from __future__ import annotations

import csv
import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from trading_agent.metrics import PaperTrade
from trading_agent.trade_cohort_buckets import (
    dollar_volume_bucket,
    gap_bucket,
    price_bucket,
    volume_to_adv_bucket,
)
from trading_agent.trade_cohort_models import (
    FeatureStatus,
    TradeFeatureAssignment,
    TradeFeatureSource,
)


class _RiskRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: dt.datetime
    exchange: str
    symbol: str
    selected: bool
    reason: str
    change_pct: float
    price: float
    bid: float
    ask: float
    spread_bps: float
    estimated_round_trip_cost_bps: float
    dollar_volume: float
    volume: int
    average_daily_volume: int
    volume_to_adv: float | None


class _GapRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    ranking_observed_at: dt.datetime
    quote_observed_at: dt.datetime
    exchange: str
    symbol: str
    status: str
    previous_close: float | None
    session_open: float | None
    opening_gap_pct: float | None
    current_price: float | None
    current_volume: int | None
    previous_volume: int | None
    error: str


@dataclass(frozen=True, slots=True)
class _Decision:
    recommendation_id: str
    symbol: str
    created_at: dt.datetime


@dataclass(frozen=True, slots=True)
class _CandidateInput:
    exchange: str
    symbol: str
    observed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class _JoinInputs:
    candidates: tuple[_CandidateInput, ...]
    risks: tuple[_RiskRow, ...]
    gaps: tuple[_GapRow, ...]


@dataclass(frozen=True, slots=True)
class TradeCohortSourceError(RuntimeError):
    path: Path

    def __str__(self) -> str:
        return f"거래 cohort 원천을 해석할 수 없습니다: {self.path}"


def load_trade_feature_assignments(
    source: TradeFeatureSource,
    trades: tuple[PaperTrade, ...],
) -> tuple[TradeFeatureAssignment, ...]:
    decisions = _decisions(source.database)
    inputs = _JoinInputs(
        _candidate_inputs(source.database),
        _risk_rows(source.risk_path),
        () if source.gap_path is None else _gap_rows(source.gap_path),
    )
    return tuple(_assignment(trade, decisions.get(trade.recommendation_id), inputs) for trade in trades)


def _assignment(
    trade: PaperTrade,
    decision: _Decision | None,
    inputs: _JoinInputs,
) -> TradeFeatureAssignment:
    if decision is None:
        return _censored(trade, trade.entry_at, "recommendation_decision_missing")
    matching_candidates = tuple(
        row
        for row in inputs.candidates
        if row.symbol == decision.symbol and _same_instant(row.observed_at, decision.created_at)
    )
    if len(matching_candidates) != 1:
        return _censored(trade, decision.created_at, "point_in_time_candidate_input_missing")
    candidate = matching_candidates[0]
    eligible_risks = tuple(
        row
        for row in inputs.risks
        if row.exchange == candidate.exchange
        and row.symbol == decision.symbol
        and row.selected
        and not row.reason
        and _at_or_before(row.observed_at, decision.created_at)
    )
    if not eligible_risks:
        return _censored(trade, decision.created_at, "point_in_time_risk_missing")
    risk = max(eligible_risks, key=lambda row: row.observed_at.astimezone(dt.UTC))
    if risk.volume_to_adv is None:
        return _censored(trade, decision.created_at, "point_in_time_volume_to_adv_missing")
    gap = _latest_gap(inputs.gaps, candidate, decision)
    return TradeFeatureAssignment(
        recommendation_id=trade.recommendation_id,
        symbol=trade.symbol,
        decision_at=decision.created_at,
        status=FeatureStatus.COMPLETE,
        reason="",
        exchange=candidate.exchange,
        candidate_observed_at=candidate.observed_at,
        risk_observed_at=risk.observed_at,
        gap_observed_at=None if gap is None else gap.quote_observed_at,
        price=risk.price,
        change_pct=risk.change_pct,
        opening_gap_pct=None if gap is None else gap.opening_gap_pct,
        volume_to_adv=risk.volume_to_adv,
        dollar_volume=risk.dollar_volume,
        spread_bps=risk.spread_bps,
        price_bucket=price_bucket(risk.price),
        gap_bucket=None if gap is None or gap.opening_gap_pct is None else gap_bucket(gap.opening_gap_pct),
        volume_to_adv_bucket=volume_to_adv_bucket(risk.volume_to_adv),
        dollar_volume_bucket=dollar_volume_bucket(risk.dollar_volume),
    )


def _latest_gap(
    gaps: tuple[_GapRow, ...],
    candidate: _CandidateInput,
    decision: _Decision,
) -> _GapRow | None:
    eligible = tuple(
        row
        for row in gaps
        if row.exchange == candidate.exchange
        and row.symbol == decision.symbol
        and row.status == "ok"
        and row.opening_gap_pct is not None
        and _at_or_before(row.ranking_observed_at, decision.created_at)
        and _at_or_before(row.quote_observed_at, decision.created_at)
    )
    return None if not eligible else max(eligible, key=lambda row: row.quote_observed_at.astimezone(dt.UTC))


def _decisions(path: Path) -> dict[str, _Decision]:
    try:
        with sqlite3.connect(path) as connection:
            rows: list[tuple[str, str, str]] = connection.execute(
                "SELECT recommendation_id, symbol, created_at FROM recommendations"
            ).fetchall()
    except sqlite3.Error as error:
        raise TradeCohortSourceError(path) from error
    return {row[0]: _Decision(row[0], row[1], dt.datetime.fromisoformat(row[2])) for row in rows}


def _candidate_inputs(path: Path) -> tuple[_CandidateInput, ...]:
    try:
        with sqlite3.connect(path) as connection:
            present = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='candidate_input_snapshots'"
            ).fetchone()
            if present is None or int(present[0]) == 0:
                return ()
            rows: list[tuple[str, str, str]] = connection.execute(
                "SELECT exchange, symbol, observed_at FROM candidate_input_snapshots"
            ).fetchall()
    except sqlite3.Error as error:
        raise TradeCohortSourceError(path) from error
    return tuple(_CandidateInput(row[0], row[1], dt.datetime.fromisoformat(row[2])) for row in rows)


def _risk_rows(path: Path) -> tuple[_RiskRow, ...]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return tuple(_RiskRow.model_validate(row) for row in csv.DictReader(handle))
    except (OSError, csv.Error, ValidationError) as error:
        raise TradeCohortSourceError(path) from error


def _gap_rows(path: Path) -> tuple[_GapRow, ...]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return tuple(_GapRow.model_validate(row) for row in csv.DictReader(handle))
    except (OSError, csv.Error, ValidationError) as error:
        raise TradeCohortSourceError(path) from error


def _censored(trade: PaperTrade, decision_at: dt.datetime, reason: str) -> TradeFeatureAssignment:
    return TradeFeatureAssignment(
        trade.recommendation_id,
        trade.symbol,
        decision_at,
        FeatureStatus.CENSORED,
        reason,
    )


def _same_instant(left: dt.datetime, right: dt.datetime) -> bool:
    return left.tzinfo is not None and right.tzinfo is not None and left.astimezone(dt.UTC) == right.astimezone(dt.UTC)


def _at_or_before(left: dt.datetime, right: dt.datetime) -> bool:
    return left.tzinfo is not None and right.tzinfo is not None and left.astimezone(dt.UTC) <= right.astimezone(dt.UTC)
