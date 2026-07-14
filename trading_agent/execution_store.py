from __future__ import annotations

import datetime as dt
import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, final, override

from trading_agent.execution_schema import (
    CREATE_SCHEMA,
    SCHEMA_VERSION,
    AccountBindingRow,
    BrokerEventRow,
    BrokerEventValues,
    IntentRow,
    StoredBrokerEvent,
    StoredIntent,
    broker_event_values,
    intent_values,
    stored_broker_event,
    stored_intent,
)
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerEventKey,
    BrokerOrderEvent,
    BrokerOrderEventType,
    IntentId,
    PaperOrderIntent,
)

TERMINAL_BROKER_EVENTS: Final = (
    BrokerOrderEventType.FILL,
    BrokerOrderEventType.REJECTED,
    BrokerOrderEventType.CANCELED,
    BrokerOrderEventType.EXPIRED,
)


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


class AccountBindingConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "실행 원장이 다른 Alpaca paper 계좌에 이미 결합되어 있습니다"


@final
class ExecutionWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def save_intent(self, intent: PaperOrderIntent, quantity: int) -> bool:
        self._require_active()
        values = intent_values(intent, quantity)
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

    def bind_account(
        self,
        account_fingerprint: AccountFingerprint,
        bound_at: dt.datetime,
    ) -> bool:
        self._require_active()
        existing: AccountBindingRow | None = self._connection.execute(
            "SELECT account_fingerprint, bound_at FROM account_binding WHERE binding_id = 1"
        ).fetchone()
        if existing is not None:
            if existing[0] != account_fingerprint:
                raise AccountBindingConflictError
            return False
        _ = self._connection.execute(
            """INSERT INTO account_binding
            (binding_id, account_fingerprint, bound_at) VALUES (1, ?, ?)""",
            (account_fingerprint, bound_at.isoformat()),
        )
        self._connection.commit()
        return True

    def append_broker_event(self, event: BrokerOrderEvent) -> bool:
        self._require_active()
        values = broker_event_values(event)
        existing: BrokerEventValues | None = (
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
        return tuple(stored_intent(row) for row in rows)

    def is_initialized(self) -> bool:
        if not self.path.is_file():
            return False
        with self._reader_connection() as connection:
            row: tuple[int] | None = connection.execute(
                "PRAGMA user_version"
            ).fetchone()
        return row == (SCHEMA_VERSION,)

    def unresolved_intent_ids(self) -> frozenset[IntentId]:
        if not self.path.is_file():
            return frozenset()
        terminal_values = tuple(event.value for event in TERMINAL_BROKER_EVENTS)
        with self._reader_connection() as connection:
            rows: list[tuple[str]] = connection.execute(
                """SELECT intent.intent_id FROM order_intents AS intent
                LEFT JOIN broker_order_events AS event ON event.event_id = (
                  SELECT MAX(candidate.event_id) FROM broker_order_events AS candidate
                  WHERE candidate.intent_id = intent.intent_id
                )
                WHERE event.event_type IS NULL
                   OR event.event_type NOT IN (?, ?, ?, ?)
                ORDER BY intent.intent_id""",
                terminal_values,
            ).fetchall()
        return frozenset(IntentId(row[0]) for row in rows)

    def account_fingerprint(self) -> AccountFingerprint | None:
        if not self.path.is_file():
            return None
        with self._reader_connection() as connection:
            row: tuple[str] | None = connection.execute(
                "SELECT account_fingerprint FROM account_binding WHERE binding_id = 1"
            ).fetchone()
        return None if row is None else AccountFingerprint(row[0])

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
        return tuple(stored_broker_event(row) for row in rows)

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
