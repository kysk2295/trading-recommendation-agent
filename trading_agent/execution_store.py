from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final, final, override

from trading_agent.paper_execution_models import (
    BrokerEventKey,
    BrokerOrderEvent,
    BrokerOrderEventType,
    BrokerOrderId,
    IntentId,
    PaperOrderIntent,
    PaperOrderSide,
)

SCHEMA_VERSION: Final = 1
CREATE_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS order_intents (
  intent_id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  symbol TEXT NOT NULL,
  created_at TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
  entry_limit TEXT NOT NULL,
  stop TEXT NOT NULL,
  target_1r TEXT NOT NULL,
  target_2r TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK(quantity > 0)
);
CREATE TABLE IF NOT EXISTS broker_order_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE,
  intent_id TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  broker_order_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  FOREIGN KEY(intent_id) REFERENCES order_intents(intent_id)
);
CREATE TRIGGER IF NOT EXISTS order_intents_no_update
BEFORE UPDATE ON order_intents BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_intents_no_delete
BEFORE DELETE ON order_intents BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS broker_events_no_update
BEFORE UPDATE ON broker_order_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS broker_events_no_delete
BEFORE DELETE ON broker_order_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

IntentRow = tuple[str, str, str, str, str, str, str, str, str, str, int]
BrokerEventRow = tuple[int, str, str, str, str, str, str]


class WriterLeaseUnavailableError(RuntimeError):
    __slots__ = ("lock_path",)

    def __init__(self, lock_path: Path) -> None:
        super().__init__()
        self.lock_path = lock_path

    @override
    def __str__(self) -> str:
        return f"Paper execution writer가 이미 실행 중입니다: {self.lock_path}"


class InactiveExecutionWriterError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Paper execution writer 사용 구간이 종료되었습니다"


class IntentConflictError(RuntimeError):
    __slots__ = ("intent_id",)

    def __init__(self, intent_id: IntentId) -> None:
        super().__init__()
        self.intent_id = intent_id

    @override
    def __str__(self) -> str:
        return f"같은 intent ID의 immutable 필드가 다릅니다: {self.intent_id}"


class BrokerEventConflictError(RuntimeError):
    __slots__ = ("event_key",)

    def __init__(self, event_key: BrokerEventKey) -> None:
        super().__init__()
        self.event_key = event_key

    @override
    def __str__(self) -> str:
        return f"같은 broker event key의 immutable 필드가 다릅니다: {self.event_key}"


@dataclass(frozen=True, slots=True)
class StoredIntent:
    intent_id: IntentId
    strategy_id: str
    strategy_version: str
    symbol: str
    created_at: str
    side: PaperOrderSide
    entry_limit: Decimal
    stop: Decimal
    target_1r: Decimal
    target_2r: Decimal
    quantity: int


@dataclass(frozen=True, slots=True)
class StoredBrokerEvent:
    event_id: int
    event_key: BrokerEventKey
    intent_id: IntentId
    occurred_at: str
    event_type: BrokerOrderEventType
    broker_order_id: BrokerOrderId
    payload_json: str


@final
class ExecutionWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def save_intent(self, intent: PaperOrderIntent, quantity: int) -> bool:
        self._require_active()
        values = _intent_values(intent, quantity)
        existing: IntentRow | None = self._connection.execute(
            "SELECT * FROM order_intents WHERE intent_id = ?",
            (intent.intent_id,),
        ).fetchone()
        if existing is not None:
            if existing != values:
                raise IntentConflictError(intent.intent_id)
            return False
        _ = self._connection.execute(
            "INSERT INTO order_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        self._connection.commit()
        return True

    def append_broker_event(self, event: BrokerOrderEvent) -> bool:
        self._require_active()
        values = _broker_event_values(event)
        existing: tuple[str, str, str, str, str, str] | None = (
            self._connection.execute(
                """SELECT event_key, intent_id, occurred_at, event_type,
                broker_order_id, payload_json FROM broker_order_events
                WHERE event_key = ?""",
                (event.event_key,),
            ).fetchone()
        )
        if existing is not None:
            if existing != values:
                raise BrokerEventConflictError(event.event_key)
            return False
        _ = self._connection.execute(
            """INSERT INTO broker_order_events
            (event_key, intent_id, occurred_at, event_type, broker_order_id, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)""",
            values,
        )
        self._connection.commit()
        return True

    def _require_active(self) -> None:
        if not self._active:
            raise InactiveExecutionWriterError

    def _close(self) -> None:
        self._active = False
        self._connection.close()


@final
class ExecutionStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    @contextmanager
    def writer(self) -> Iterator[ExecutionWriter]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WriterLeaseUnavailableError(lock_path) from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            _prepare_writer_connection(connection)
            writer = ExecutionWriter(connection)
            try:
                yield writer
            finally:
                writer._close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def intents(self) -> tuple[StoredIntent, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[IntentRow] = connection.execute(
                "SELECT * FROM order_intents ORDER BY created_at, intent_id"
            ).fetchall()
        return tuple(_stored_intent(row) for row in rows)

    def broker_events(self, intent_id: IntentId) -> tuple[StoredBrokerEvent, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[BrokerEventRow] = connection.execute(
                """SELECT event_id, event_key, intent_id, occurred_at, event_type,
                broker_order_id, payload_json FROM broker_order_events
                WHERE intent_id = ? ORDER BY event_id""",
                (intent_id,),
            ).fetchall()
        return tuple(_stored_broker_event(row) for row in rows)

    def _reader_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        _ = connection.execute("PRAGMA query_only = ON")
        _ = connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _prepare_writer_connection(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    _ = connection.execute("PRAGMA busy_timeout = 0")
    _ = connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(CREATE_SCHEMA)
    _ = connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.commit()


def _intent_values(intent: PaperOrderIntent, quantity: int) -> IntentRow:
    return (
        intent.intent_id,
        intent.strategy_id,
        intent.strategy_version,
        intent.symbol,
        intent.created_at.isoformat(),
        intent.side.value,
        str(intent.entry_limit),
        str(intent.stop),
        str(intent.target_1r),
        str(intent.target_2r),
        quantity,
    )


def _broker_event_values(event: BrokerOrderEvent) -> tuple[str, str, str, str, str, str]:
    return (
        event.event_key,
        event.intent_id,
        event.occurred_at.isoformat(),
        event.event_type.value,
        event.broker_order_id,
        event.payload_json,
    )


def _stored_intent(row: IntentRow) -> StoredIntent:
    return StoredIntent(
        IntentId(row[0]), row[1], row[2], row[3], row[4], PaperOrderSide(row[5]),
        Decimal(row[6]), Decimal(row[7]), Decimal(row[8]), Decimal(row[9]), row[10]
    )


def _stored_broker_event(row: BrokerEventRow) -> StoredBrokerEvent:
    return StoredBrokerEvent(
        row[0], BrokerEventKey(row[1]), IntentId(row[2]), row[3],
        BrokerOrderEventType(row[4]), BrokerOrderId(row[5]), row[6]
    )
