from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from scr_backtest.kis_intraday import KisMinuteBar
from trading_agent.kis_live import NEW_YORK, regular_session_is_open
from trading_agent.kis_provider import KisRankedStock

CREATE_CANDIDATE_BARS: Final = (
    "CREATE TABLE IF NOT EXISTS candidate_minute_bars ("
    "exchange TEXT NOT NULL, symbol TEXT NOT NULL, "
    "exchange_timestamp TEXT NOT NULL, first_observed_at TEXT NOT NULL, "
    "korea_timestamp TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, "
    "low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL, "
    "amount INTEGER NOT NULL, PRIMARY KEY(exchange, symbol, exchange_timestamp))"
)
INSERT_CANDIDATE_BAR: Final = "INSERT OR IGNORE INTO candidate_minute_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
CREATE_CANDIDATE_INPUTS: Final = (
    "CREATE TABLE IF NOT EXISTS candidate_input_snapshots ("
    "exchange TEXT NOT NULL, symbol TEXT NOT NULL, observed_at TEXT NOT NULL, "
    "latest_completed_bar_at TEXT NOT NULL, prior_close REAL NOT NULL, "
    "average_daily_volume INTEGER NOT NULL, spread_bps REAL NOT NULL, "
    "PRIMARY KEY(exchange, symbol, observed_at))"
)
INSERT_CANDIDATE_INPUT: Final = "INSERT OR IGNORE INTO candidate_input_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)"
CREATE_TRACKED_CANDIDATES: Final = (
    "CREATE TABLE IF NOT EXISTS tracked_candidates ("
    "session_date TEXT NOT NULL, exchange TEXT NOT NULL, symbol TEXT NOT NULL, "
    "first_observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL, "
    "name TEXT NOT NULL, price REAL NOT NULL, change_pct REAL NOT NULL, "
    "bid REAL NOT NULL, ask REAL NOT NULL, volume INTEGER NOT NULL, "
    "dollar_volume REAL NOT NULL, average_daily_volume INTEGER NOT NULL, "
    "source_rank INTEGER NOT NULL, PRIMARY KEY(session_date, exchange, symbol))"
)
UPSERT_TRACKED_CANDIDATE: Final = (
    "INSERT INTO tracked_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(session_date, exchange, symbol) DO UPDATE SET "
    "last_observed_at = excluded.last_observed_at, name = excluded.name, "
    "price = excluded.price, change_pct = excluded.change_pct, "
    "bid = excluded.bid, ask = excluded.ask, volume = excluded.volume, "
    "dollar_volume = excluded.dollar_volume, "
    "average_daily_volume = excluded.average_daily_volume, "
    "source_rank = excluded.source_rank"
)
TrackedCandidateRow = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    float,
    float,
    float,
    float,
    int,
    float,
    int,
    int,
]


@dataclass(frozen=True, slots=True)
class CandidateBarBatch:
    exchange: str
    symbol: str
    observed_at: dt.datetime
    bars: tuple[KisMinuteBar, ...]


@dataclass(frozen=True, slots=True)
class CandidateInputSnapshot:
    exchange: str
    symbol: str
    observed_at: dt.datetime
    latest_completed_bar_at: dt.datetime
    prior_close: float
    average_daily_volume: int
    spread_bps: float


def archive_candidate_bars(path: Path, batch: CandidateBarBatch) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        _ = connection.execute(CREATE_CANDIDATE_BARS)
        cursor = connection.executemany(
            INSERT_CANDIDATE_BAR,
            (
                (
                    batch.exchange,
                    batch.symbol,
                    bar.exchange_timestamp.isoformat(),
                    batch.observed_at.isoformat(),
                    bar.korea_timestamp.isoformat(),
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                    bar.amount,
                )
                for bar in batch.bars
            ),
        )
    return cursor.rowcount


def archive_candidate_input(path: Path, snapshot: CandidateInputSnapshot) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        _ = connection.execute(CREATE_CANDIDATE_INPUTS)
        cursor = connection.execute(
            INSERT_CANDIDATE_INPUT,
            (
                snapshot.exchange,
                snapshot.symbol,
                snapshot.observed_at.isoformat(),
                snapshot.latest_completed_bar_at.isoformat(),
                snapshot.prior_close,
                snapshot.average_daily_volume,
                snapshot.spread_bps,
            ),
        )
    return cursor.rowcount


def track_candidates(
    path: Path,
    observed_at: dt.datetime,
    candidates: tuple[KisRankedStock, ...],
) -> int:
    if not regular_session_is_open(observed_at):
        return 0
    session_date = observed_at.astimezone(NEW_YORK).date().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        _ = connection.execute(CREATE_TRACKED_CANDIDATES)
        cursor = connection.executemany(
            UPSERT_TRACKED_CANDIDATE,
            (
                (
                    session_date,
                    stock.exchange,
                    stock.symbol,
                    observed_at.isoformat(),
                    observed_at.isoformat(),
                    stock.name,
                    stock.price,
                    stock.change_pct,
                    stock.bid,
                    stock.ask,
                    stock.volume,
                    stock.dollar_volume,
                    stock.average_daily_volume,
                    stock.rank,
                )
                for stock in candidates
            ),
        )
    return cursor.rowcount


def tracked_candidates(
    path: Path,
    observed_at: dt.datetime,
) -> tuple[KisRankedStock, ...]:
    if not path.is_file() or not regular_session_is_open(observed_at):
        return ()
    return tracked_candidates_for_session(
        path,
        observed_at.astimezone(NEW_YORK).date(),
    )


def tracked_candidates_for_session(
    path: Path,
    session_date: dt.date,
) -> tuple[KisRankedStock, ...]:
    if not path.is_file():
        return ()
    with sqlite3.connect(path) as connection:
        present: tuple[int] | None = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='tracked_candidates'"
        ).fetchone()
        if present is None or present[0] == 0:
            return ()
        rows: list[TrackedCandidateRow] = connection.execute(
            "SELECT * FROM tracked_candidates WHERE session_date = ? ORDER BY first_observed_at, exchange, symbol",
            (session_date.isoformat(),),
        ).fetchall()
    return tuple(
        KisRankedStock(
            exchange=row[1],
            symbol=row[2],
            name=row[5],
            price=float(row[6]),
            change_pct=float(row[7]),
            bid=float(row[8]),
            ask=float(row[9]),
            volume=int(row[10]),
            dollar_volume=float(row[11]),
            average_daily_volume=int(row[12]),
            rank=int(row[13]),
        )
        for row in rows
    )
