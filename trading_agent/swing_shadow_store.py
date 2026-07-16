from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Final, Self, final, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.signal_contract_models import TradeSignalEnvelope

_SCHEMA_VERSION: Final = 1
_SOURCE_KEY = re.compile(r"^[0-9a-f]{64}$")


class InvalidSwingShadowLedgerError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing shadow ledger를 안전하게 확인하지 못했습니다"


class SwingShadowConflictError(ValueError):
    @override
    def __str__(self) -> str:
        return "동일 US swing shadow 식별자에 서로 다른 내용이 있습니다"


class SwingShadowWriterLeaseUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "US swing shadow ledger Writer lease를 획득하지 못했습니다"


class InactiveSwingShadowWriterError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "종료된 US swing shadow ledger Writer는 사용할 수 없습니다"


class ShadowEventKind(StrEnum):
    SIGNAL_CREATED = "signal_created"
    ENTRY_FILLED = "entry_filled"
    STOPPED = "stopped"
    TARGETED = "targeted"
    TIME_EXIT = "time_exit"
    EXPIRED = "expired"


class SwingShadowEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_id: str
    kind: ShadowEventKind
    session_date: dt.date
    observed_at: dt.datetime
    source_key: str
    price: Decimal | None = None

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        requires_price = self.kind in {
            ShadowEventKind.ENTRY_FILLED,
            ShadowEventKind.STOPPED,
            ShadowEventKind.TARGETED,
            ShadowEventKind.TIME_EXIT,
        }
        if (
            not _canonical_text(self.signal_id)
            or not _aware(self.observed_at)
            or _SOURCE_KEY.fullmatch(self.source_key) is None
            or (requires_price and not _positive_finite(self.price))
            or (not requires_price and self.price is not None)
        ):
            raise ValueError("invalid swing shadow event")
        return self

    @property
    def event_id(self) -> str:
        return f"{self.signal_id}:{self.kind.value}"


class SwingShadowReader:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def is_initialized(self) -> bool:
        if not self.path.is_file():
            return False
        try:
            with self.reader_connection() as connection:
                version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
        except sqlite3.Error:
            return False
        return version == (_SCHEMA_VERSION,)

    def events(self, signal_id: str) -> tuple[SwingShadowEvent, ...]:
        if not self.path.is_file():
            return ()
        with self.reader_connection() as connection:
            rows: list[tuple[str]] = connection.execute(
                "SELECT payload_json FROM swing_shadow_events WHERE signal_id = ? ORDER BY sequence",
                (signal_id,),
            ).fetchall()
        return _events_from_rows(rows)

    def signals(self) -> tuple[TradeSignalEnvelope, ...]:
        if not self.path.is_file():
            return ()
        with self.reader_connection() as connection:
            rows: list[tuple[str]] = connection.execute(
                "SELECT payload_json FROM swing_shadow_signals ORDER BY rowid"
            ).fetchall()
        return _signals_from_rows(rows)

    def reader_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        _ = connection.execute("PRAGMA query_only = ON")
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _require_current_schema(connection)
        return connection


@final
class SwingShadowStore(SwingShadowReader):
    __slots__ = ()

    @contextmanager
    def writer(self) -> Iterator[SwingShadowWriter]:
        if self.path.is_symlink():
            raise InvalidSwingShadowLedgerError
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        _validate_lock_path(lock_path)
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise InvalidSwingShadowLedgerError
        try:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | no_follow, 0o600)
        except OSError as error:
            raise InvalidSwingShadowLedgerError from error
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SwingShadowWriterLeaseUnavailableError from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            os.chmod(self.path, 0o600)
            try:
                _prepare_writer_connection(connection)
                writer = SwingShadowWriter(connection)
                try:
                    yield writer
                finally:
                    writer._close()
            finally:
                connection.close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@final
class SwingShadowWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def append_signal(
        self,
        signal: TradeSignalEnvelope,
        *,
        session_date: dt.date,
        source_key: str,
    ) -> SwingShadowEvent | None:
        self._require_active()
        try:
            validated = TradeSignalEnvelope.model_validate(signal.model_dump(mode="python"))
            payload = _canonical_payload(validated)
        except (TypeError, ValidationError, ValueError):
            raise InvalidSwingShadowLedgerError from None
        existing = self._connection.execute(
            "SELECT payload_json FROM swing_shadow_signals WHERE signal_id = ?",
            (validated.signal_id,),
        ).fetchone()
        if existing is not None:
            if existing[0] != payload:
                raise SwingShadowConflictError
            return None
        event = SwingShadowEvent(
            signal_id=validated.signal_id,
            kind=ShadowEventKind.SIGNAL_CREATED,
            session_date=session_date,
            observed_at=validated.observed_at,
            source_key=source_key,
        )
        try:
            with self._connection:
                _ = self._connection.execute(
                    "INSERT INTO swing_shadow_signals (signal_id, payload_json) VALUES (?, ?)",
                    (validated.signal_id, payload),
                )
                self._append_event(event)
        except sqlite3.IntegrityError as error:
            raise SwingShadowConflictError from error
        return event

    def append_event(self, event: SwingShadowEvent) -> bool:
        self._require_active()
        try:
            validated = SwingShadowEvent.model_validate(event.model_dump(mode="python"))
            with self._connection:
                return self._append_event(validated)
        except SwingShadowConflictError:
            raise
        except (sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidSwingShadowLedgerError from None

    def events(self, signal_id: str) -> tuple[SwingShadowEvent, ...]:
        self._require_active()
        rows: list[tuple[str]] = self._connection.execute(
            "SELECT payload_json FROM swing_shadow_events WHERE signal_id = ? ORDER BY sequence",
            (signal_id,),
        ).fetchall()
        return _events_from_rows(rows)

    def signals(self) -> tuple[TradeSignalEnvelope, ...]:
        self._require_active()
        rows: list[tuple[str]] = self._connection.execute(
            "SELECT payload_json FROM swing_shadow_signals ORDER BY rowid"
        ).fetchall()
        return _signals_from_rows(rows)

    def _append_event(self, event: SwingShadowEvent) -> bool:
        payload = _canonical_payload(event)
        existing = self._connection.execute(
            "SELECT payload_json FROM swing_shadow_events WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()
        if existing is not None:
            if existing[0] != payload:
                raise SwingShadowConflictError
            return False
        sequence_row: tuple[int] = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM swing_shadow_events WHERE signal_id = ?",
            (event.signal_id,),
        ).fetchone()
        _ = self._connection.execute(
            """INSERT INTO swing_shadow_events
            (event_id, signal_id, sequence, event_kind, payload_json)
            VALUES (?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.signal_id,
                sequence_row[0],
                event.kind.value,
                payload,
            ),
        )
        return True

    def _close(self) -> None:
        self._active = False

    def _require_active(self) -> None:
        if not self._active:
            raise InactiveSwingShadowWriterError


def _prepare_writer_connection(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    version: tuple[int] = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(
            """
            CREATE TABLE swing_shadow_signals (
                signal_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE swing_shadow_events (
                event_id TEXT PRIMARY KEY,
                signal_id TEXT NOT NULL REFERENCES swing_shadow_signals(signal_id),
                sequence INTEGER NOT NULL,
                event_kind TEXT NOT NULL CHECK(event_kind IN (
                    'signal_created', 'entry_filled', 'stopped',
                    'targeted', 'time_exit', 'expired'
                )),
                payload_json TEXT NOT NULL,
                UNIQUE(signal_id, sequence)
            );
            CREATE TRIGGER swing_shadow_signals_no_update
            BEFORE UPDATE ON swing_shadow_signals
            BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            CREATE TRIGGER swing_shadow_signals_no_delete
            BEFORE DELETE ON swing_shadow_signals
            BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            CREATE TRIGGER swing_shadow_events_no_update
            BEFORE UPDATE ON swing_shadow_events
            BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            CREATE TRIGGER swing_shadow_events_no_delete
            BEFORE DELETE ON swing_shadow_events
            BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            PRAGMA user_version = 1;
            """
        )
    else:
        _require_current_schema(connection)


def _validate_lock_path(path: Path) -> None:
    if not path.exists():
        return
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
    ):
        raise InvalidSwingShadowLedgerError


def _require_current_schema(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version != (_SCHEMA_VERSION,):
        raise InvalidSwingShadowLedgerError


def _signals_from_rows(rows: list[tuple[str]]) -> tuple[TradeSignalEnvelope, ...]:
    try:
        return tuple(TradeSignalEnvelope.model_validate_json(payload) for (payload,) in rows)
    except (TypeError, ValidationError, ValueError) as error:
        raise InvalidSwingShadowLedgerError from error


def _events_from_rows(rows: list[tuple[str]]) -> tuple[SwingShadowEvent, ...]:
    try:
        return tuple(SwingShadowEvent.model_validate_json(payload) for (payload,) in rows)
    except (TypeError, ValidationError, ValueError) as error:
        raise InvalidSwingShadowLedgerError from error


def _canonical_payload(value: BaseModel) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip() and len(value) <= 512 and not any(
        character in value for character in "\r\n\t"
    )


def _positive_finite(value: Decimal | None) -> bool:
    return value is not None and value.is_finite() and value > 0


__all__ = (
    "InactiveSwingShadowWriterError",
    "InvalidSwingShadowLedgerError",
    "ShadowEventKind",
    "SwingShadowConflictError",
    "SwingShadowEvent",
    "SwingShadowReader",
    "SwingShadowStore",
    "SwingShadowWriter",
    "SwingShadowWriterLeaseUnavailableError",
)
