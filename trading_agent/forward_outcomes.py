from __future__ import annotations

import csv
import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from trading_agent.kis_live import NEW_YORK, regular_session_bounds


@dataclass(frozen=True, slots=True)
class SelectedSnapshot:
    observed_at: dt.datetime
    exchange: str
    symbol: str
    price: float
    change_pct: float
    spread_bps: float
    dollar_volume: float


@dataclass(frozen=True, slots=True)
class ArchivedBar:
    exchange: str
    symbol: str
    timestamp: dt.datetime
    first_observed_at: dt.datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class ForwardOutcome:
    observed_at: dt.datetime
    exchange: str
    symbol: str
    scanner_price: float
    change_pct: float
    spread_bps: float
    dollar_volume: float
    entry_at: dt.datetime | None
    entry: float | None
    bar_count: int
    complete: bool
    return_5m: float | None
    return_15m: float | None
    return_30m: float | None
    eod_return: float | None
    mfe: float | None
    mae: float | None


def analyze_forward_outcomes(
    snapshot_path: Path,
    database_path: Path,
) -> tuple[ForwardOutcome, ...]:
    snapshots = _read_selected_snapshots(snapshot_path)
    bars = _read_archived_bars(database_path)
    grouped: dict[tuple[str, str], list[ArchivedBar]] = {}
    for bar in bars:
        grouped.setdefault((bar.exchange, bar.symbol), []).append(bar)
    outcomes = (
        _measure(snapshot, tuple(grouped.get((snapshot.exchange, snapshot.symbol), ())))
        for snapshot in snapshots
        if _is_regular_observation(snapshot.observed_at)
    )
    return tuple(sorted(outcomes, key=lambda row: (row.observed_at, row.exchange, row.symbol)))


def _measure(
    snapshot: SelectedSnapshot,
    bars: tuple[ArchivedBar, ...],
) -> ForwardOutcome:
    observed_at = snapshot.observed_at.astimezone(NEW_YORK)
    entry_at = observed_at.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    session_bars = tuple(
        bar
        for bar in bars
        if bar.timestamp.astimezone(NEW_YORK).date() == observed_at.date()
        and bar.timestamp >= entry_at
    )
    if not session_bars:
        return _empty_outcome(snapshot)
    entry = session_bars[0].open
    bounds = regular_session_bounds(observed_at.date())
    expected_count = 0 if bounds is None else int((bounds[1] - entry_at) / dt.timedelta(minutes=1))
    complete = len(session_bars) == expected_count and all(
        bar.timestamp == entry_at + dt.timedelta(minutes=index)
        for index, bar in enumerate(session_bars)
    )
    if not complete:
        return _incomplete_outcome(snapshot, session_bars[0], len(session_bars))
    return ForwardOutcome(
        snapshot.observed_at,
        snapshot.exchange,
        snapshot.symbol,
        snapshot.price,
        snapshot.change_pct,
        snapshot.spread_bps,
        snapshot.dollar_volume,
        session_bars[0].timestamp,
        entry,
        len(session_bars),
        True,
        _return_at(session_bars, 5, entry),
        _return_at(session_bars, 15, entry),
        _return_at(session_bars, 30, entry),
        session_bars[-1].close / entry - 1.0,
        max(bar.high for bar in session_bars) / entry - 1.0,
        min(bar.low for bar in session_bars) / entry - 1.0,
    )


def _return_at(
    bars: tuple[ArchivedBar, ...],
    minutes: int,
    entry: float,
) -> float | None:
    return None if len(bars) < minutes else bars[minutes - 1].close / entry - 1.0


def _empty_outcome(snapshot: SelectedSnapshot) -> ForwardOutcome:
    return ForwardOutcome(
        snapshot.observed_at,
        snapshot.exchange,
        snapshot.symbol,
        snapshot.price,
        snapshot.change_pct,
        snapshot.spread_bps,
        snapshot.dollar_volume,
        None,
        None,
        0,
        False,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def _incomplete_outcome(
    snapshot: SelectedSnapshot,
    first: ArchivedBar,
    bar_count: int,
) -> ForwardOutcome:
    empty = _empty_outcome(snapshot)
    return ForwardOutcome(
        empty.observed_at,
        empty.exchange,
        empty.symbol,
        empty.scanner_price,
        empty.change_pct,
        empty.spread_bps,
        empty.dollar_volume,
        first.timestamp,
        first.open,
        bar_count,
        False,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def _is_regular_observation(observed_at: dt.datetime) -> bool:
    current = observed_at.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    return bounds is not None and bounds[0] <= current < bounds[1]


def _read_selected_snapshots(path: Path) -> tuple[SelectedSnapshot, ...]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    selected = tuple(
        SelectedSnapshot(
            dt.datetime.fromisoformat(row["observed_at"]),
            row["exchange"],
            row["symbol"],
            float(row["price"]),
            float(row["change_pct"]),
            float(row["spread_bps"]),
            float(row["dollar_volume"]),
        )
        for row in rows
        if row.get("selection_input") == "True"
    )
    first_by_symbol_day: dict[tuple[dt.date, str, str], SelectedSnapshot] = {}
    for snapshot in sorted(selected, key=lambda row: row.observed_at):
        key = (
            snapshot.observed_at.astimezone(NEW_YORK).date(),
            snapshot.exchange,
            snapshot.symbol,
        )
        first_by_symbol_day.setdefault(key, snapshot)
    return tuple(first_by_symbol_day.values())


def _read_archived_bars(path: Path) -> tuple[ArchivedBar, ...]:
    if not path.is_file():
        return ()
    with sqlite3.connect(path) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'candidate_minute_bars'"
        ).fetchone()
        if table is None:
            return ()
        rows: list[tuple[str, str, str, str, float, float, float, float]] = connection.execute(
            "SELECT exchange, symbol, exchange_timestamp, first_observed_at, "
            "open, high, low, close FROM candidate_minute_bars "
            "ORDER BY exchange, symbol, exchange_timestamp"
        ).fetchall()
    return tuple(
        ArchivedBar(
            row[0],
            row[1],
            dt.datetime.fromisoformat(row[2]),
            dt.datetime.fromisoformat(row[3]),
            float(row[4]),
            float(row[5]),
            float(row[6]),
            float(row[7]),
        )
        for row in rows
    )
