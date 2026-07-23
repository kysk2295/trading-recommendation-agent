from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NewType, final, override

from pydantic import ValidationError

from trading_agent.systematic_regime_review_models import SystematicRegimeReviewEvent

_SCHEMA_VERSION: Final = 1
SystematicRegimeReviewEventKey = NewType("SystematicRegimeReviewEventKey", str)


class InvalidSystematicRegimeReviewSourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime review ledger is invalid"


class SystematicRegimeReviewConflictError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime review identity has conflicting content"


class SystematicRegimeReviewWriterLeaseUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "US systematic regime review Writer lease is unavailable"


class InactiveSystematicRegimeReviewWriterError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "US systematic regime review Writer is inactive"


@dataclass(frozen=True, slots=True)
class StoredSystematicRegimeReviewEvent:
    event_key: SystematicRegimeReviewEventKey
    event: SystematicRegimeReviewEvent


class SystematicRegimeReviewReader:
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

    def events(self) -> tuple[StoredSystematicRegimeReviewEvent, ...]:
        if not self.path.is_file():
            return ()
        with self.reader_connection() as connection:
            rows: list[tuple[str, str, str, str]] = connection.execute(
                """SELECT event_key, card_id, reviewer_version, payload_json
                FROM systematic_regime_review_events ORDER BY rowid"""
            ).fetchall()
        return tuple(_stored_event(row) for row in rows)

    def review_event(
        self,
        card_id: str,
        reviewer_version: str,
    ) -> StoredSystematicRegimeReviewEvent | None:
        if not self.path.is_file():
            return None
        with self.reader_connection() as connection:
            rows: list[tuple[str, str, str, str]] = connection.execute(
                """SELECT event_key, card_id, reviewer_version, payload_json
                FROM systematic_regime_review_events
                WHERE card_id = ? AND reviewer_version = ?""",
                (card_id, reviewer_version),
            ).fetchall()
        if len(rows) > 1:
            raise InvalidSystematicRegimeReviewSourceError
        if not rows:
            return None
        stored = _stored_event(rows[0])
        if stored.event.card_id != card_id or stored.event.reviewer_version != reviewer_version:
            raise InvalidSystematicRegimeReviewSourceError
        return stored

    @contextmanager
    def reader_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            _ = connection.execute("PRAGMA query_only = ON")
            _ = connection.execute("PRAGMA foreign_keys = ON")
            _require_current_schema(connection)
            yield connection
        finally:
            connection.close()


@final
class SystematicRegimeReviewStore(SystematicRegimeReviewReader):
    __slots__ = ()

    @contextmanager
    def writer(self) -> Iterator[SystematicRegimeReviewWriter]:
        if self.path.is_symlink():
            raise InvalidSystematicRegimeReviewSourceError
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        _validate_lock_path(lock_path)
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise InvalidSystematicRegimeReviewSourceError
        try:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | no_follow, 0o600)
        except OSError:
            raise InvalidSystematicRegimeReviewSourceError from None
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SystematicRegimeReviewWriterLeaseUnavailableError from None
            connection = sqlite3.connect(self.path, timeout=0.0)
            os.chmod(self.path, 0o600)
            try:
                _prepare_writer_connection(connection)
                writer = SystematicRegimeReviewWriter(connection)
                try:
                    yield writer
                finally:
                    writer._close()
            finally:
                connection.close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@final
class SystematicRegimeReviewWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def append_event(self, event: SystematicRegimeReviewEvent) -> bool:
        self._require_active()
        try:
            validated = SystematicRegimeReviewEvent.model_validate(event.model_dump(mode="python"))
            payload = canonical_systematic_regime_review_json(validated)
        except (TypeError, ValidationError, ValueError):
            raise InvalidSystematicRegimeReviewSourceError from None
        key = systematic_regime_review_event_key(validated)
        existing = self._connection.execute(
            "SELECT payload_json FROM systematic_regime_review_events WHERE event_key = ?",
            (key,),
        ).fetchone()
        if existing is not None:
            if existing[0] == payload:
                return False
            raise SystematicRegimeReviewConflictError
        identity = self._connection.execute(
            """SELECT payload_json FROM systematic_regime_review_events
            WHERE card_id = ? AND reviewer_version = ?""",
            (validated.card_id, validated.reviewer_version),
        ).fetchone()
        if identity is not None:
            raise SystematicRegimeReviewConflictError
        try:
            with self._connection:
                _ = self._connection.execute(
                    "INSERT INTO systematic_regime_review_events VALUES (?, ?, ?, ?)",
                    (key, validated.card_id, validated.reviewer_version, payload),
                )
        except sqlite3.IntegrityError:
            raise SystematicRegimeReviewConflictError from None
        return True

    def _require_active(self) -> None:
        if not self._active:
            raise InactiveSystematicRegimeReviewWriterError

    def _close(self) -> None:
        self._active = False


def canonical_systematic_regime_review_json(event: SystematicRegimeReviewEvent) -> str:
    return json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def systematic_regime_review_event_key(
    event: SystematicRegimeReviewEvent,
) -> SystematicRegimeReviewEventKey:
    payload = canonical_systematic_regime_review_json(event)
    return SystematicRegimeReviewEventKey(hashlib.sha256(payload.encode()).hexdigest())


def _prepare_writer_connection(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    version: tuple[int] = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(
            """
            CREATE TABLE systematic_regime_review_events (
                event_key TEXT PRIMARY KEY,
                card_id TEXT NOT NULL,
                reviewer_version TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(card_id, reviewer_version)
            );
            CREATE TRIGGER systematic_regime_review_events_no_update
            BEFORE UPDATE ON systematic_regime_review_events
            BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            CREATE TRIGGER systematic_regime_review_events_no_delete
            BEFORE DELETE ON systematic_regime_review_events
            BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            PRAGMA user_version = 1;
            """
        )
    else:
        _require_current_schema(connection)


def _stored_event(row: tuple[str, str, str, str]) -> StoredSystematicRegimeReviewEvent:
    key, card_id, reviewer_version, payload = row
    try:
        event = SystematicRegimeReviewEvent.model_validate_json(payload)
    except ValueError:
        raise InvalidSystematicRegimeReviewSourceError from None
    event_key = SystematicRegimeReviewEventKey(key)
    if (
        event_key != systematic_regime_review_event_key(event)
        or event.card_id != card_id
        or event.reviewer_version != reviewer_version
    ):
        raise InvalidSystematicRegimeReviewSourceError
    return StoredSystematicRegimeReviewEvent(event_key, event)


def _require_current_schema(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version != (_SCHEMA_VERSION,):
        raise InvalidSystematicRegimeReviewSourceError


def _validate_lock_path(path: Path) -> None:
    if not path.exists():
        return
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise InvalidSystematicRegimeReviewSourceError


__all__ = (
    "InactiveSystematicRegimeReviewWriterError",
    "InvalidSystematicRegimeReviewSourceError",
    "StoredSystematicRegimeReviewEvent",
    "SystematicRegimeReviewConflictError",
    "SystematicRegimeReviewEventKey",
    "SystematicRegimeReviewReader",
    "SystematicRegimeReviewStore",
    "SystematicRegimeReviewWriterLeaseUnavailableError",
    "canonical_systematic_regime_review_json",
    "systematic_regime_review_event_key",
)
