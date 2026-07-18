from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import final, override

from trading_agent.intraday_feature_kernel import CompletedMinuteBar
from trading_agent.us_market_data_runtime_codec import (
    bar_from_row,
    datetime_from_text,
    incident_from_row,
    incident_key,
    incident_row,
    receipt_row,
)
from trading_agent.us_market_data_runtime_models import (
    MarketDataRuntimeCheckpoint,
    MarketDataRuntimeError,
    MarketDataRuntimeIncident,
    MarketDataRuntimeReceipt,
    validate_market_data_runtime_receipt,
)
from trading_agent.us_market_data_runtime_schema import (
    CREATE_MARKET_DATA_RUNTIME_SCHEMA,
    MARKET_DATA_RUNTIME_SCHEMA_VERSION,
)


class MarketDataWriterLeaseUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "market data runtime writer is already active"


class UnsupportedMarketDataRuntimeSchemaError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "market data runtime schema is unsupported"


class InactiveMarketDataRuntimeWriterError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "market data runtime writer is inactive"


class MarketDataRuntimeReader:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def receipt_count(self, source_id: str) -> int:
        if not self.path.is_file():
            return 0
        with self._reader_connection() as connection:
            row: tuple[int] = connection.execute(
                "SELECT count(*) FROM market_data_runtime_receipts WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return row[0]

    def incidents(self, source_id: str) -> tuple[MarketDataRuntimeIncident, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows = connection.execute(
                """SELECT kind, source_id, previous_epoch, current_epoch,
                expected_sequence, observed_sequence, recorded_at
                FROM market_data_runtime_incidents WHERE source_id = ? ORDER BY rowid""",
                (source_id,),
            ).fetchall()
        return tuple(incident_from_row(row) for row in rows)

    def _reader_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        _ = connection.execute("PRAGMA query_only = ON")
        _require_schema(connection)
        return connection


@final
class MarketDataRuntimeStore(MarketDataRuntimeReader):
    __slots__ = ()

    @contextmanager
    def writer(self) -> Iterator[MarketDataRuntimeWriter]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise MarketDataWriterLeaseUnavailableError from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            os.chmod(self.path, 0o600)
            try:
                _prepare_writer_connection(connection)
                writer = MarketDataRuntimeWriter(connection)
                try:
                    yield writer
                finally:
                    writer.close()
            finally:
                connection.close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@final
class MarketDataRuntimeWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def latest_checkpoint(self, source_id: str) -> MarketDataRuntimeCheckpoint | None:
        self._require_active()
        row = self._connection.execute(
            """SELECT source_id, connection_epoch, last_sequence, gap_blocked, recorded_at
            FROM market_data_runtime_checkpoints WHERE source_id = ?
            ORDER BY generation DESC LIMIT 1""",
            (source_id,),
        ).fetchone()
        if row is None:
            return None
        return MarketDataRuntimeCheckpoint(row[0], row[1], row[2], bool(row[3]), datetime_from_text(row[4]))

    def append_receipt(self, receipt: MarketDataRuntimeReceipt) -> bool:
        self._require_active()
        validate_market_data_runtime_receipt(receipt)
        row = receipt_row(receipt)
        existing = self._connection.execute(
            """SELECT source_id, connection_epoch, sequence, receipt_id, received_at,
            payload_sha256, raw_payload, instrument_id, symbol, bar_start_at, bar_end_at,
            open, high, low, close, volume FROM market_data_runtime_receipts
            WHERE source_id = ? AND connection_epoch = ? AND sequence = ?""",
            (receipt.source_id, receipt.connection_epoch, receipt.sequence),
        ).fetchone()
        if existing is not None:
            if tuple(existing) == row:
                return False
            raise MarketDataRuntimeError
        try:
            _ = self._connection.execute(
                "INSERT INTO market_data_runtime_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise MarketDataRuntimeError from error
        return True

    def append_incident(self, incident: MarketDataRuntimeIncident) -> bool:
        self._require_active()
        row = incident_row(incident)
        key = incident_key(row)
        existing = self._connection.execute(
            """SELECT kind, source_id, previous_epoch, current_epoch, expected_sequence,
            observed_sequence, recorded_at FROM market_data_runtime_incidents
            WHERE incident_key = ?""",
            (key,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) == row:
                return False
            raise MarketDataRuntimeError
        _ = self._connection.execute(
            "INSERT INTO market_data_runtime_incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (key, *row),
        )
        self._connection.commit()
        return True

    def append_checkpoint(self, checkpoint: MarketDataRuntimeCheckpoint) -> None:
        self._require_active()
        _ = self._connection.execute(
            """INSERT INTO market_data_runtime_checkpoints
            (source_id, connection_epoch, last_sequence, gap_blocked, recorded_at)
            VALUES (?, ?, ?, ?, ?)""",
            (
                checkpoint.source_id,
                checkpoint.connection_epoch,
                checkpoint.last_sequence,
                int(checkpoint.gap_blocked),
                checkpoint.recorded_at.isoformat(),
            ),
        )
        self._connection.commit()

    def completed_bars(
        self, source_id: str, connection_epoch: str, instrument_id: str
    ) -> tuple[CompletedMinuteBar, ...]:
        self._require_active()
        rows = self._connection.execute(
            """SELECT bar_start_at, bar_end_at, open, high, low, close, volume
            FROM market_data_runtime_receipts
            WHERE source_id = ? AND connection_epoch = ? AND instrument_id = ?
            ORDER BY sequence""",
            (source_id, connection_epoch, instrument_id),
        ).fetchall()
        return tuple(bar_from_row(row) for row in rows)

    def close(self) -> None:
        self._active = False

    def _require_active(self) -> None:
        if not self._active:
            raise InactiveMarketDataRuntimeWriterError


def _prepare_writer_connection(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    version = connection.execute("PRAGMA user_version").fetchone()
    current = 0 if version is None else version[0]
    if current == 0:
        objects = tuple(
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        )
        if objects:
            raise UnsupportedMarketDataRuntimeSchemaError
        connection.executescript(CREATE_MARKET_DATA_RUNTIME_SCHEMA)
        _ = connection.execute(f"PRAGMA user_version = {MARKET_DATA_RUNTIME_SCHEMA_VERSION}")
        connection.commit()
        return
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (MARKET_DATA_RUNTIME_SCHEMA_VERSION,):
        raise UnsupportedMarketDataRuntimeSchemaError


__all__ = (
    "MarketDataRuntimeStore",
    "MarketDataWriterLeaseUnavailableError",
)
