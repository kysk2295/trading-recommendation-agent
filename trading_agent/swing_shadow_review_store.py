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

from trading_agent.swing_shadow_review_models import (
    SwingShadowReviewEvent,
)

_SCHEMA_VERSION: Final = 1
SwingShadowReviewEventKey = NewType("SwingShadowReviewEventKey", str)


class InvalidSwingShadowReviewSourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing shadow review ledger의 immutable event 근거가 유효하지 않습니다"


class SwingShadowReviewConflictError(ValueError):
    @override
    def __str__(self) -> str:
        return "동일 US swing shadow review 식별자에 서로 다른 내용이 있습니다"


class SwingShadowReviewWriterLeaseUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "US swing shadow review ledger Writer lease를 획득하지 못했습니다"


class InactiveSwingShadowReviewWriterError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "종료된 US swing shadow review Writer는 사용할 수 없습니다"


@dataclass(frozen=True, slots=True)
class StoredSwingShadowReviewEvent:
    event_key: SwingShadowReviewEventKey
    event: SwingShadowReviewEvent


class SwingShadowReviewReader:
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

    def events(self) -> tuple[StoredSwingShadowReviewEvent, ...]:
        if not self.path.is_file():
            return ()
        with self.reader_connection() as connection:
            rows: list[tuple[str, str, str, str]] = connection.execute(
                """SELECT event_key, signal_id, reviewer_version, payload_json
                FROM swing_shadow_review_events ORDER BY rowid"""
            ).fetchall()
        return tuple(_stored_event(row) for row in rows)

    def review_event(
        self,
        signal_id: str,
        reviewer_version: str,
    ) -> StoredSwingShadowReviewEvent | None:
        if not self.path.is_file():
            return None
        with self.reader_connection() as connection:
            rows: list[tuple[str, str, str, str]] = connection.execute(
                """SELECT event_key, signal_id, reviewer_version, payload_json
                FROM swing_shadow_review_events
                WHERE signal_id = ? AND reviewer_version = ?""",
                (signal_id, reviewer_version),
            ).fetchall()
        if len(rows) > 1:
            raise InvalidSwingShadowReviewSourceError
        if not rows:
            return None
        stored = _stored_event(rows[0])
        if stored.event.signal_id != signal_id or stored.event.reviewer_version != reviewer_version:
            raise InvalidSwingShadowReviewSourceError
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
class SwingShadowReviewStore(SwingShadowReviewReader):
    __slots__ = ()

    @contextmanager
    def writer(self) -> Iterator[SwingShadowReviewWriter]:
        if self.path.is_symlink():
            raise InvalidSwingShadowReviewSourceError
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        _validate_lock_path(lock_path)
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise InvalidSwingShadowReviewSourceError
        try:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | no_follow, 0o600)
        except OSError as error:
            raise InvalidSwingShadowReviewSourceError from error
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SwingShadowReviewWriterLeaseUnavailableError from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            os.chmod(self.path, 0o600)
            try:
                _prepare_writer_connection(connection)
                writer = SwingShadowReviewWriter(connection)
                try:
                    yield writer
                finally:
                    writer._close()
            finally:
                connection.close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@final
class SwingShadowReviewWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def append_event(self, event: SwingShadowReviewEvent) -> bool:
        self._require_active()
        try:
            validated = SwingShadowReviewEvent.model_validate(event.model_dump(mode="python"))
            payload = canonical_swing_shadow_review_json(validated)
        except (TypeError, ValidationError, ValueError):
            raise InvalidSwingShadowReviewSourceError from None
        key = swing_shadow_review_event_key(validated)
        existing = self._connection.execute(
            "SELECT payload_json FROM swing_shadow_review_events WHERE event_key = ?",
            (key,),
        ).fetchone()
        if existing is not None:
            if existing[0] == payload:
                return False
            raise SwingShadowReviewConflictError
        identity = self._connection.execute(
            """SELECT payload_json FROM swing_shadow_review_events
            WHERE signal_id = ? AND reviewer_version = ?""",
            (validated.signal_id, validated.reviewer_version),
        ).fetchone()
        if identity is not None:
            raise SwingShadowReviewConflictError
        try:
            with self._connection:
                _ = self._connection.execute(
                    "INSERT INTO swing_shadow_review_events VALUES (?, ?, ?, ?)",
                    (key, validated.signal_id, validated.reviewer_version, payload),
                )
        except sqlite3.IntegrityError as error:
            raise SwingShadowReviewConflictError from error
        return True

    def _require_active(self) -> None:
        if not self._active:
            raise InactiveSwingShadowReviewWriterError

    def _close(self) -> None:
        self._active = False


def canonical_swing_shadow_review_json(event: SwingShadowReviewEvent) -> str:
    return json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def swing_shadow_review_event_key(event: SwingShadowReviewEvent) -> SwingShadowReviewEventKey:
    payload = canonical_swing_shadow_review_json(event)
    return SwingShadowReviewEventKey(hashlib.sha256(payload.encode()).hexdigest())


def _prepare_writer_connection(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    version: tuple[int] = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(
            """
            CREATE TABLE swing_shadow_review_events (
                event_key TEXT PRIMARY KEY,
                signal_id TEXT NOT NULL,
                reviewer_version TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(signal_id, reviewer_version)
            );
            CREATE TRIGGER swing_shadow_review_events_no_update
            BEFORE UPDATE ON swing_shadow_review_events
            BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            CREATE TRIGGER swing_shadow_review_events_no_delete
            BEFORE DELETE ON swing_shadow_review_events
            BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            PRAGMA user_version = 1;
            """
        )
    else:
        _require_current_schema(connection)


def _stored_event(row: tuple[str, str, str, str]) -> StoredSwingShadowReviewEvent:
    key, signal_id, reviewer_version, payload = row
    try:
        event = SwingShadowReviewEvent.model_validate_json(payload)
    except ValueError:
        raise InvalidSwingShadowReviewSourceError from None
    event_key = SwingShadowReviewEventKey(key)
    if (
        event_key != swing_shadow_review_event_key(event)
        or event.signal_id != signal_id
        or event.reviewer_version != reviewer_version
    ):
        raise InvalidSwingShadowReviewSourceError
    return StoredSwingShadowReviewEvent(event_key, event)


def _require_current_schema(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version != (_SCHEMA_VERSION,):
        raise InvalidSwingShadowReviewSourceError


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
        raise InvalidSwingShadowReviewSourceError


__all__ = (
    "InactiveSwingShadowReviewWriterError",
    "InvalidSwingShadowReviewSourceError",
    "StoredSwingShadowReviewEvent",
    "SwingShadowReviewConflictError",
    "SwingShadowReviewEventKey",
    "SwingShadowReviewReader",
    "SwingShadowReviewStore",
    "SwingShadowReviewWriter",
    "SwingShadowReviewWriterLeaseUnavailableError",
    "canonical_swing_shadow_review_json",
    "swing_shadow_review_event_key",
)
