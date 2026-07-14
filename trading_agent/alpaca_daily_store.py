from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from trading_agent.alpaca_reference import AlpacaDailyReference

CREATE_DAILY_BARS: Final = """CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    PRIMARY KEY (symbol, session_date)
) WITHOUT ROWID"""
CREATE_BATCHES: Final = """CREATE TABLE IF NOT EXISTS completed_batches (
    batch_index INTEGER PRIMARY KEY,
    symbols_json TEXT NOT NULL,
    request_count INTEGER NOT NULL,
    bar_count INTEGER NOT NULL,
    status TEXT NOT NULL
)"""
CREATE_METADATA: Final = """CREATE TABLE IF NOT EXISTS cache_metadata (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID"""
CREATE_SESSION_INDEX: Final = "CREATE INDEX IF NOT EXISTS daily_bars_session_date ON daily_bars (session_date, symbol)"


def initialize_cache(
    connection: sqlite3.Connection,
    start_date: dt.date,
    end_date: dt.date,
    symbol_count: int,
) -> None:
    connection.execute(CREATE_DAILY_BARS)
    connection.execute(CREATE_BATCHES)
    connection.execute(CREATE_METADATA)
    connection.executemany(
        "INSERT OR REPLACE INTO cache_metadata VALUES (?, ?)",
        (
            ("status", "building"),
            ("start_date", start_date.isoformat()),
            ("end_date", end_date.isoformat()),
            ("symbol_count", str(symbol_count)),
            ("feed", "sip"),
            ("adjustment", "raw"),
            ("selection_uses_only_dates_before_target", "true"),
        ),
    )
    connection.commit()


def finalize_cache(connection: sqlite3.Connection, request_count: int) -> int:
    connection.execute(CREATE_SESSION_INDEX)
    bar_count = int(connection.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0])
    connection.executemany(
        "INSERT OR REPLACE INTO cache_metadata VALUES (?, ?)",
        (
            ("status", "complete"),
            ("request_count", str(request_count)),
            ("bar_count", str(bar_count)),
        ),
    )
    connection.commit()
    return bar_count


def completed_batch(
    connection: sqlite3.Connection,
    index: int,
    symbols: tuple[str, ...],
) -> int | None:
    row = connection.execute(
        "SELECT symbols_json, request_count, status FROM completed_batches WHERE batch_index = ?",
        (index,),
    ).fetchone()
    if row is None or row[2] != "complete" or tuple(json.loads(row[0])) != symbols:
        return None
    return int(row[1])


def insert_bars(
    connection: sqlite3.Connection,
    rows: Iterable[tuple[str, str, float, int]],
) -> int:
    cursor = connection.executemany("INSERT OR IGNORE INTO daily_bars VALUES (?, ?, ?, ?)", rows)
    return cursor.rowcount


def complete_batch(
    connection: sqlite3.Connection,
    index: int,
    symbols: tuple[str, ...],
    request_count: int,
    bar_count: int,
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO completed_batches VALUES (?, ?, ?, ?, 'complete')",
        (index, json.dumps(symbols), request_count, bar_count),
    )
    connection.commit()


def load_references(
    database_path: Path,
    session_date: dt.date,
    symbols: tuple[str, ...],
    lookback_calendar_days: int,
    reference_sessions: int,
    minimum_reference_sessions: int,
) -> tuple[AlpacaDailyReference, ...]:
    requested = tuple(sorted(set(symbols)))
    requested_set = set(requested)
    first_date = session_date - dt.timedelta(days=lookback_calendar_days)
    found: dict[str, AlpacaDailyReference] = {}
    with sqlite3.connect(database_path) as connection:
        _validate_session(connection, session_date)
        rows = connection.execute(
            "SELECT symbol, session_date, close, volume FROM daily_bars "
            "WHERE session_date >= ? AND session_date < ? ORDER BY symbol, session_date",
            (first_date.isoformat(), session_date.isoformat()),
        )
        current_symbol: str | None = None
        history: list[tuple[dt.date, float, int]] = []
        for symbol, raw_date, close, volume in rows:
            if symbol != current_symbol:
                _store_reference(
                    found,
                    current_symbol,
                    history,
                    requested_set,
                    reference_sessions,
                    minimum_reference_sessions,
                )
                current_symbol = str(symbol)
                history = []
            history.append((dt.date.fromisoformat(str(raw_date)), float(close), int(volume)))
        _store_reference(
            found,
            current_symbol,
            history,
            requested_set,
            reference_sessions,
            minimum_reference_sessions,
        )
    return tuple(found.get(symbol, _missing_reference(symbol)) for symbol in requested)


def _validate_session(connection: sqlite3.Connection, session_date: dt.date) -> None:
    metadata = dict(connection.execute("SELECT name, value FROM cache_metadata"))
    start_date = dt.date.fromisoformat(metadata["start_date"])
    end_date = dt.date.fromisoformat(metadata["end_date"])
    if metadata.get("status") != "complete" or not start_date <= session_date <= end_date:
        raise ValueError("요청 거래일이 완료된 일봉 범위 캐시 밖에 있습니다")


def _store_reference(
    found: dict[str, AlpacaDailyReference],
    symbol: str | None,
    history: list[tuple[dt.date, float, int]],
    requested: set[str],
    reference_sessions: int,
    minimum_reference_sessions: int,
) -> None:
    if symbol is None or symbol not in requested:
        return
    recent = history[-reference_sessions:]
    if len(recent) < minimum_reference_sessions:
        found[symbol] = _missing_reference(symbol, len(recent))
        return
    found[symbol] = AlpacaDailyReference(
        symbol=symbol,
        prior_session=recent[-1][0],
        prior_close=recent[-1][1],
        average_volume=sum(item[2] for item in recent) / len(recent),
        history_sessions=len(recent),
    )


def _missing_reference(symbol: str, history_sessions: int = 0) -> AlpacaDailyReference:
    return AlpacaDailyReference(symbol, None, None, None, history_sessions)
