from __future__ import annotations

import datetime as dt
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from trading_agent.daily_research_contract import strategy_contract
from trading_agent.lane_defaults import INTRADAY_PILOT_PAPER_RISK_CONFIG
from trading_agent.paper_execution_models import (
    IntentId,
    PaperOrderIntent,
    PaperOrderSide,
)
from trading_agent.paper_operating_session_models import PaperOrderAdmissionRequest
from trading_agent.paper_order_gate_models import LatestCompletedBar
from trading_agent.strategy_factory import StrategyMode
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

ORB_STRATEGY: Final = "opening_range_breakout"
ORB_RESEARCH_CONTRACT: Final = strategy_contract(StrategyMode.ORB)
SOURCE_MAX_AGE: Final = dt.timedelta(seconds=30)
BAR_DURATION: Final = dt.timedelta(minutes=1)
MAX_CLIENT_ORDER_ID_LENGTH: Final = 128


class InvalidCurrentOrbPaperEntrySourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "현재 ORB Paper entry source를 안전하게 확정하지 못했습니다"


@dataclass(frozen=True, slots=True)
class _RecommendationSource:
    recommendation_id: str
    symbol: str
    strategy: str
    created_at: dt.datetime
    entry: float
    stop: float
    target_1r: float
    target_2r: float
    state: str


@dataclass(frozen=True, slots=True)
class _CandidateInputSource:
    exchange: str
    symbol: str
    observed_at: dt.datetime
    latest_completed_bar_at: dt.datetime
    spread_bps: float


@dataclass(frozen=True, slots=True)
class _CandidateBarSource:
    exchange: str
    symbol: str
    started_at: dt.datetime
    first_observed_at: dt.datetime
    volume: int


def load_current_orb_paper_entry(
    path: Path,
    evaluated_at: dt.datetime,
) -> PaperOrderAdmissionRequest:
    if not path.is_file() or not _is_aware(evaluated_at):
        raise InvalidCurrentOrbPaperEntrySourceError
    try:
        with closing(_connect_readonly(path)) as connection:
            _ = connection.execute("BEGIN")
            recommendations = _recommendations(connection)
            inputs = _candidate_inputs(connection)
            bars = _candidate_bars(connection)
        requests = _current_requests(
            recommendations,
            inputs,
            bars,
            evaluated_at,
        )
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        raise InvalidCurrentOrbPaperEntrySourceError from None
    if len(requests) != 1:
        raise InvalidCurrentOrbPaperEntrySourceError
    return requests[0]


def _connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    _ = connection.execute("PRAGMA query_only = ON")
    return connection


def _recommendations(
    connection: sqlite3.Connection,
) -> tuple[_RecommendationSource, ...]:
    rows: list[tuple[str, str, str, str, float, float, float, float, str]] = connection.execute(
        "SELECT recommendation_id, symbol, strategy, created_at, entry, "
        "stop, target_1r, target_2r, state FROM recommendations "
        "WHERE strategy = ? AND state = ? ORDER BY created_at, symbol",
        (ORB_STRATEGY, "setup"),
    ).fetchall()
    return tuple(
        _RecommendationSource(
            row[0],
            row[1],
            row[2],
            _aware_datetime(row[3]),
            float(row[4]),
            float(row[5]),
            float(row[6]),
            float(row[7]),
            row[8],
        )
        for row in rows
    )


def _candidate_inputs(
    connection: sqlite3.Connection,
) -> tuple[_CandidateInputSource, ...]:
    rows: list[tuple[str, str, str, str, float]] = connection.execute(
        "SELECT exchange, symbol, observed_at, latest_completed_bar_at, "
        "spread_bps FROM candidate_input_snapshots "
        "ORDER BY observed_at, exchange, symbol"
    ).fetchall()
    return tuple(
        _CandidateInputSource(
            row[0],
            row[1],
            _aware_datetime(row[2]),
            _aware_datetime(row[3]),
            float(row[4]),
        )
        for row in rows
    )


def _candidate_bars(
    connection: sqlite3.Connection,
) -> tuple[_CandidateBarSource, ...]:
    rows: list[tuple[str, str, str, str, int]] = connection.execute(
        "SELECT exchange, symbol, exchange_timestamp, first_observed_at, "
        "volume FROM candidate_minute_bars "
        "ORDER BY exchange_timestamp, exchange, symbol"
    ).fetchall()
    return tuple(
        _CandidateBarSource(
            row[0],
            row[1],
            _aware_datetime(row[2]),
            _aware_datetime(row[3]),
            int(row[4]),
        )
        for row in rows
    )


def _current_requests(
    recommendations: tuple[_RecommendationSource, ...],
    inputs: tuple[_CandidateInputSource, ...],
    bars: tuple[_CandidateBarSource, ...],
    evaluated_at: dt.datetime,
) -> tuple[PaperOrderAdmissionRequest, ...]:
    expected_start = evaluated_at.astimezone(NEW_YORK).replace(second=0, microsecond=0) - BAR_DURATION
    requests: list[PaperOrderAdmissionRequest] = []
    for recommendation in recommendations:
        for candidate_input in inputs:
            if not _input_matches(recommendation, candidate_input):
                continue
            for bar in bars:
                if not _bar_matches(candidate_input, bar):
                    continue
                if _source_is_valid(
                    recommendation,
                    candidate_input,
                    bar,
                    expected_start,
                    evaluated_at,
                ):
                    requests.append(_request(recommendation, candidate_input, bar))
    return tuple(requests)


def _input_matches(
    recommendation: _RecommendationSource,
    candidate_input: _CandidateInputSource,
) -> bool:
    return candidate_input.symbol == recommendation.symbol and candidate_input.observed_at == recommendation.created_at


def _bar_matches(
    candidate_input: _CandidateInputSource,
    bar: _CandidateBarSource,
) -> bool:
    return (
        bar.exchange == candidate_input.exchange
        and bar.symbol == candidate_input.symbol
        and bar.started_at == candidate_input.latest_completed_bar_at
    )


def _source_is_valid(
    recommendation: _RecommendationSource,
    candidate_input: _CandidateInputSource,
    bar: _CandidateBarSource,
    expected_start: dt.datetime,
    evaluated_at: dt.datetime,
) -> bool:
    bounds = regular_session_bounds(expected_start.date())
    prices = (
        recommendation.entry,
        recommendation.stop,
        recommendation.target_1r,
        recommendation.target_2r,
        candidate_input.spread_bps,
    )
    age = evaluated_at.astimezone(dt.UTC) - recommendation.created_at.astimezone(dt.UTC)
    return (
        bounds is not None
        and bounds[0] <= expected_start
        and expected_start + BAR_DURATION <= bounds[1]
        and recommendation.strategy == ORB_STRATEGY
        and recommendation.state == "setup"
        and recommendation.symbol != ""
        and recommendation.symbol == recommendation.symbol.upper()
        and 0 < len(recommendation.recommendation_id) <= MAX_CLIENT_ORDER_ID_LENGTH
        and recommendation.recommendation_id
        == f"{recommendation.created_at.isoformat()}:{recommendation.symbol}:{ORB_STRATEGY}"
        and all(math.isfinite(value) for value in prices)
        and 0 < recommendation.stop < recommendation.entry < recommendation.target_1r < recommendation.target_2r
        and candidate_input.exchange != ""
        and candidate_input.exchange == candidate_input.exchange.upper()
        and candidate_input.latest_completed_bar_at.astimezone(NEW_YORK) == expected_start
        and bar.started_at.astimezone(NEW_YORK) == expected_start
        and bar.started_at + BAR_DURATION <= bar.first_observed_at <= recommendation.created_at <= evaluated_at
        and dt.timedelta(0) <= age <= SOURCE_MAX_AGE
        and candidate_input.spread_bps >= 0
        and bar.volume > 0
    )


def _request(
    recommendation: _RecommendationSource,
    candidate_input: _CandidateInputSource,
    bar: _CandidateBarSource,
) -> PaperOrderAdmissionRequest:
    return PaperOrderAdmissionRequest(
        LatestCompletedBar(
            recommendation.symbol,
            bar.started_at,
            bar.first_observed_at,
        ),
        PaperOrderIntent(
            IntentId(recommendation.recommendation_id),
            StrategyMode.ORB.value,
            ORB_RESEARCH_CONTRACT.strategy_version,
            recommendation.symbol,
            recommendation.created_at,
            PaperOrderSide.BUY,
            recommendation.entry,
            recommendation.stop,
            recommendation.target_1r,
            recommendation.target_2r,
        ),
        1,
        candidate_input.spread_bps,
        INTRADAY_PILOT_PAPER_RISK_CONFIG,
    )


def _aware_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if not _is_aware(parsed):
        raise ValueError("timestamp must have an offset")
    return parsed


def _is_aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
