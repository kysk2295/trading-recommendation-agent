from __future__ import annotations

import csv
import datetime as dt
import math
import sqlite3
from pathlib import Path
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict

from trading_agent.challenger_replay_models import (
    ReplayBar,
    ReplayContext,
    ReplaySource,
    ReplaySourceRejectedError,
    ReplaySymbolCoverage,
)
from trading_agent.daily_research_sources import load_session_quality
from trading_agent.kis_live import NEW_YORK, regular_session_bounds

DATABASE_NAME: Final = "paper_recommendations.sqlite3"
ContextRow = tuple[str, str, str, str, float, int, float]
BarRow = tuple[str, str, str, str, float, float, float, float, int]


class _CycleRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    started_at: AwareDatetime
    exit_code: int


def load_replay_source(session: Path) -> ReplaySource:
    if not session.is_dir():
        raise ReplaySourceRejectedError(("source_session_missing",))
    database = session / DATABASE_NAME
    if not database.is_file():
        raise ReplaySourceRejectedError(("source_database_missing",))
    quality_reasons: list[str] = []
    try:
        quality, incidents = load_session_quality(session, completed_trades=0)
        watch_rows = _load_cycle_rows(session / "watch_cycles.csv")
        post_rows = _load_cycle_rows(session / "post_session_metrics_cycles.csv")
    except (OSError, sqlite3.Error, ValueError):
        raise ReplaySourceRejectedError(("source_quality_unreadable",)) from None
    if not quality.forward_day_eligible:
        quality_reasons.extend(("forward_day_ineligible", *incidents))
    session_date = _single_session_date(watch_rows)
    if session_date is None:
        quality_reasons.append("watch_session_date_invalid")
    successful_post_rows = tuple(row for row in post_rows if row.exit_code == 0)
    if not successful_post_rows:
        quality_reasons.append("post_session_metrics_missing_or_failed")
    elif session_date is not None and not _post_session_rows_are_valid(successful_post_rows, session_date):
        quality_reasons.append("post_session_metrics_session_mismatch")
    if quality_reasons:
        raise ReplaySourceRejectedError(tuple(dict.fromkeys(quality_reasons)), session_date)
    if session_date is None:
        raise ReplaySourceRejectedError(("watch_session_date_invalid",))
    try:
        contexts = _load_contexts(database)
        bars = _load_bars(database)
    except (OSError, sqlite3.Error, ValueError):
        raise ReplaySourceRejectedError(("source_data_unreadable",), session_date) from None
    if len(contexts) != quality.candidate_inputs:
        raise ReplaySourceRejectedError(("candidate_input_changed_during_replay_gate",), session_date)
    value_reasons = _source_value_reasons(contexts, bars)
    if value_reasons:
        raise ReplaySourceRejectedError(value_reasons, session_date)
    if any(row.observed_at.astimezone(NEW_YORK).date() != session_date for row in contexts):
        raise ReplaySourceRejectedError(("candidate_input_session_mismatch",), session_date)
    if any(row.timestamp.astimezone(NEW_YORK).date() != session_date for row in bars):
        raise ReplaySourceRejectedError(("candidate_bar_session_mismatch",), session_date)
    duplicate_symbols = _duplicate_symbols_across_exchanges(contexts)
    if duplicate_symbols:
        raise ReplaySourceRejectedError(("duplicate_symbol_across_exchanges",), session_date)
    coverage = _coverage(session_date, contexts, bars)
    return ReplaySource(session_date, contexts, bars, coverage)


def _load_cycle_rows(path: Path) -> tuple[_CycleRow, ...]:
    if not path.is_file():
        return ()
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(_CycleRow.model_validate(row) for row in csv.DictReader(handle))


def _single_session_date(rows: tuple[_CycleRow, ...]) -> dt.date | None:
    dates = {row.started_at.astimezone(NEW_YORK).date() for row in rows}
    return next(iter(dates)) if len(dates) == 1 else None


def _post_session_rows_are_valid(rows: tuple[_CycleRow, ...], session_date: dt.date) -> bool:
    bounds = regular_session_bounds(session_date)
    return bounds is not None and all(
        row.started_at.astimezone(NEW_YORK).date() == session_date and row.started_at.astimezone(NEW_YORK) >= bounds[1]
        for row in rows
    )


def _load_contexts(path: Path) -> tuple[ReplayContext, ...]:
    if not _table_exists(path, "candidate_input_snapshots"):
        return ()
    with _connect_readonly(path) as connection:
        rows: list[ContextRow] = connection.execute(
            "SELECT exchange, symbol, observed_at, latest_completed_bar_at, "
            "prior_close, average_daily_volume, spread_bps "
            "FROM candidate_input_snapshots ORDER BY observed_at, exchange, symbol"
        ).fetchall()
    return tuple(
        ReplayContext(
            row[0],
            row[1],
            _aware_datetime(row[2]),
            _aware_datetime(row[3]),
            float(row[4]),
            int(row[5]),
            float(row[6]),
        )
        for row in rows
    )


def _load_bars(path: Path) -> tuple[ReplayBar, ...]:
    if not _table_exists(path, "candidate_minute_bars"):
        return ()
    with _connect_readonly(path) as connection:
        rows: list[BarRow] = connection.execute(
            "SELECT exchange, symbol, exchange_timestamp, first_observed_at, "
            "open, high, low, close, volume FROM candidate_minute_bars "
            "ORDER BY exchange_timestamp, exchange, symbol"
        ).fetchall()
    return tuple(
        ReplayBar(
            row[0],
            row[1],
            _aware_datetime(row[2]),
            _aware_datetime(row[3]),
            float(row[4]),
            float(row[5]),
            float(row[6]),
            float(row[7]),
            int(row[8]),
        )
        for row in rows
    )


def _table_exists(path: Path, table: str) -> bool:
    if not path.is_file():
        return False
    with _connect_readonly(path) as connection:
        row: tuple[int] | None = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    return row is not None and row[0] == 1


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _aware_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ReplaySourceRejectedError(("source_timestamp_missing_offset",))
    return parsed


def _duplicate_symbols_across_exchanges(contexts: tuple[ReplayContext, ...]) -> tuple[str, ...]:
    exchanges: dict[str, set[str]] = {}
    for row in contexts:
        exchanges.setdefault(row.symbol, set()).add(row.exchange)
    return tuple(sorted(symbol for symbol, values in exchanges.items() if len(values) > 1))


def _source_value_reasons(
    contexts: tuple[ReplayContext, ...],
    bars: tuple[ReplayBar, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if any(row.latest_completed_bar_at + dt.timedelta(minutes=1) > row.observed_at for row in contexts):
        reasons.append("candidate_input_uses_unfinished_bar")
    if any(
        row.prior_close <= 0.0
        or row.average_daily_volume <= 0
        or not math.isfinite(row.prior_close)
        or not math.isfinite(row.spread_bps)
        or row.spread_bps < 0.0
        for row in contexts
    ):
        reasons.append("candidate_input_values_invalid")
    if any(row.first_observed_at < row.timestamp + dt.timedelta(minutes=1) for row in bars):
        reasons.append("candidate_bar_observed_before_completion")
    if any(not _valid_bar(row) for row in bars):
        reasons.append("candidate_bar_values_invalid")
    return tuple(reasons)


def _valid_bar(row: ReplayBar) -> bool:
    prices = (row.open, row.high, row.low, row.close)
    return (
        all(math.isfinite(value) and value > 0.0 for value in prices)
        and row.volume >= 0
        and row.low <= min(row.open, row.close)
        and row.high >= max(row.open, row.close)
    )


def _coverage(
    session_date: dt.date,
    contexts: tuple[ReplayContext, ...],
    bars: tuple[ReplayBar, ...],
) -> tuple[ReplaySymbolCoverage, ...]:
    bounds = regular_session_bounds(session_date)
    if bounds is None:
        raise ReplaySourceRejectedError(("not_a_trading_session",), session_date)
    expected = int((bounds[1] - bounds[0]).total_seconds() // 60)
    expected_times = {bounds[0] + dt.timedelta(minutes=index) for index in range(expected)}
    keys = tuple(sorted({(row.exchange, row.symbol) for row in contexts}))
    result: list[ReplaySymbolCoverage] = []
    for exchange, symbol in keys:
        actual = {
            row.timestamp.astimezone(NEW_YORK)
            for row in bars
            if (row.exchange, row.symbol) == (exchange, symbol) and bounds[0] <= row.timestamp < bounds[1]
        }
        complete = actual == expected_times
        result.append(
            ReplaySymbolCoverage(
                exchange,
                symbol,
                expected,
                len(actual),
                complete,
                "" if complete else "missing_regular_minutes",
            )
        )
    return tuple(result)
