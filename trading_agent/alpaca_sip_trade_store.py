from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import final

from trading_agent.alpaca_sip_trade_models import (
    AlpacaSipReceivedTradeFrame,
    AlpacaSipTradeHistoryError,
)

_SCHEMA_VERSION = 1
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS alpaca_sip_trade_frames ("
    "generation INTEGER PRIMARY KEY AUTOINCREMENT,receipt_id TEXT NOT NULL UNIQUE,"
    "market_date TEXT NOT NULL,received_at TEXT NOT NULL,payload_sha256 TEXT NOT NULL,"
    "payload BLOB NOT NULL);"
)


@dataclass(frozen=True, slots=True)
class StoredAlpacaSipTradeFrame:
    generation: int
    receipt_id: str
    market_date: dt.date
    received_at: dt.datetime
    payload_sha256: str
    payload: bytes = field(repr=False)


@final
class AlpacaSipTradeHistoryStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def append_frame(self, frame: AlpacaSipReceivedTradeFrame) -> StoredAlpacaSipTradeFrame:
        try:
            normalized = AlpacaSipReceivedTradeFrame(
                frame.market_date,
                frame.received_at.astimezone(dt.UTC),
                frame.payload,
            )
            receipt_id = _receipt_id(normalized)
            payload_sha256 = hashlib.sha256(normalized.payload).hexdigest()
            with _Writer(self.path) as connection:
                existing = connection.execute(
                    "SELECT generation,receipt_id,market_date,received_at,payload_sha256,payload "
                    "FROM alpaca_sip_trade_frames WHERE receipt_id=?",
                    (receipt_id,),
                ).fetchone()
                if existing is not None:
                    stored = _stored(existing)
                    if stored.payload != normalized.payload:
                        raise AlpacaSipTradeHistoryError
                    return stored
                cursor = connection.execute(
                    "INSERT INTO alpaca_sip_trade_frames "
                    "(receipt_id,market_date,received_at,payload_sha256,payload) VALUES (?,?,?,?,?)",
                    (
                        receipt_id,
                        normalized.market_date.isoformat(),
                        normalized.received_at.isoformat(),
                        payload_sha256,
                        normalized.payload,
                    ),
                )
                connection.commit()
                generation = cursor.lastrowid
                if type(generation) is not int:
                    raise AlpacaSipTradeHistoryError
                return StoredAlpacaSipTradeFrame(
                    generation,
                    receipt_id,
                    normalized.market_date,
                    normalized.received_at,
                    payload_sha256,
                    normalized.payload,
                )
        except (AttributeError, OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipTradeHistoryError from None

    def load_frames(self, market_date: dt.date) -> tuple[StoredAlpacaSipTradeFrame, ...]:
        if not self.path.is_file():
            return ()
        try:
            if type(market_date) is not dt.date or isinstance(market_date, dt.datetime):
                raise AlpacaSipTradeHistoryError
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                _require_schema(connection)
                rows = connection.execute(
                    "SELECT generation,receipt_id,market_date,received_at,payload_sha256,payload "
                    "FROM alpaca_sip_trade_frames WHERE market_date=? ORDER BY generation",
                    (market_date.isoformat(),),
                ).fetchall()
            return tuple(_stored(row) for row in rows)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipTradeHistoryError from None

    def frame_count(self) -> int:
        if not self.path.is_file():
            return 0
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                _require_schema(connection)
                row: tuple[int] = connection.execute("SELECT count(*) FROM alpaca_sip_trade_frames").fetchone()
            return row[0]
        except sqlite3.Error:
            raise AlpacaSipTradeHistoryError from None


class _Writer:
    __slots__ = ("_connection", "_handle", "_path")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle = None
        self._connection = None

    def __enter__(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(f"{self._path}.writer.lock", os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        self._handle = os.fdopen(descriptor, "a+", encoding="utf-8")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        try:
            connection = sqlite3.connect(self._path)
            os.chmod(self._path, 0o600)
            _prepare(connection)
            self._connection = connection
            return connection
        except (OSError, sqlite3.Error, ValueError):
            self._handle.close()
            self._handle = None
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = exc_type, exc_value, traceback
        if self._connection is not None:
            self._connection.close()
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()


def _receipt_id(frame: AlpacaSipReceivedTradeFrame) -> str:
    identity = {
        "market_date": frame.market_date.isoformat(),
        "payload_sha256": hashlib.sha256(frame.payload).hexdigest(),
        "received_at": frame.received_at.isoformat(),
    }
    encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _stored(row: tuple[int, str, str, str, str, bytes]) -> StoredAlpacaSipTradeFrame:
    stored = StoredAlpacaSipTradeFrame(
        row[0],
        row[1],
        dt.date.fromisoformat(row[2]),
        dt.datetime.fromisoformat(row[3]),
        row[4],
        row[5],
    )
    frame = AlpacaSipReceivedTradeFrame(stored.market_date, stored.received_at, stored.payload)
    if stored.receipt_id != _receipt_id(frame) or stored.payload_sha256 != hashlib.sha256(stored.payload).hexdigest():
        raise AlpacaSipTradeHistoryError
    return stored


def _prepare(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(_SCHEMA)
        _ = connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        connection.commit()
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
        raise AlpacaSipTradeHistoryError


__all__ = ("AlpacaSipTradeHistoryStore", "StoredAlpacaSipTradeFrame")
