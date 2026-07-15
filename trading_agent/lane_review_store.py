from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import final, override

from trading_agent.lane_review_keys import (
    LaneReviewEventKey,
    canonical_lane_review_json,
    lane_review_event_key,
)
from trading_agent.lane_review_models import LaneReviewEvent
from trading_agent.lane_review_schema import (
    CREATE_LANE_REVIEW_SCHEMA,
    LANE_REVIEW_SCHEMA_VERSION,
)


class LaneReviewConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "lane review immutable identity의 내용이 다릅니다"


class InvalidLaneReviewSourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "lane review ledger의 immutable event 근거가 유효하지 않습니다"


class LaneReviewWriterLeaseUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "lane review ledger single Writer lease를 획득하지 못했습니다"


class UnsupportedLaneReviewSchemaError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "지원하지 않는 lane review ledger schema입니다"


class InactiveLaneReviewWriterError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "종료된 lane review Writer는 사용할 수 없습니다"


@dataclass(frozen=True, slots=True)
class StoredLaneReviewEvent:
    event_key: LaneReviewEventKey
    event: LaneReviewEvent


class LaneReviewReader:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def is_initialized(self) -> bool:
        if not self.path.is_file():
            return False
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
            version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
        return version == (LANE_REVIEW_SCHEMA_VERSION,)

    def events(self) -> tuple[StoredLaneReviewEvent, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str]] = connection.execute(
                "SELECT event_key, payload_json FROM lane_review_events ORDER BY rowid"
            ).fetchall()
        return tuple(
            StoredLaneReviewEvent(
                LaneReviewEventKey(key),
                LaneReviewEvent.model_validate_json(payload),
            )
            for key, payload in rows
        )

    def review_event(
        self,
        snapshot_key: str,
        experiment_scope_key: str,
        reviewer_version: str,
    ) -> StoredLaneReviewEvent | None:
        if not self.path.is_file():
            return None
        with self._reader_connection() as connection:
            rows: list[tuple[str, str]] = connection.execute(
                """SELECT event_key, payload_json FROM lane_review_events
                WHERE snapshot_key = ? AND experiment_scope_key = ?
                AND reviewer_version = ?""",
                (snapshot_key, experiment_scope_key, reviewer_version),
            ).fetchall()
        if len(rows) > 1:
            raise InvalidLaneReviewSourceError
        if not rows:
            return None
        key, payload = rows[0]
        return StoredLaneReviewEvent(
            LaneReviewEventKey(key),
            LaneReviewEvent.model_validate_json(payload),
        )

    def _reader_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        _ = connection.execute("PRAGMA query_only = ON")
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _require_current_schema(connection)
        return connection


@final
class LaneReviewStore(LaneReviewReader):
    __slots__ = ()

    @contextmanager
    def writer(self) -> Iterator[LaneReviewWriter]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise LaneReviewWriterLeaseUnavailableError from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            os.chmod(self.path, 0o600)
            try:
                _prepare_writer_connection(connection)
                writer = LaneReviewWriter(connection)
                try:
                    yield writer
                finally:
                    writer._close()
            finally:
                connection.close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@final
class LaneReviewWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def append_event(self, event: LaneReviewEvent) -> bool:
        self._require_active()
        event = LaneReviewEvent.model_validate(event.model_dump(mode="python"))
        key = lane_review_event_key(event)
        payload = canonical_lane_review_json(event)
        existing: tuple[str] | None = self._connection.execute(
            "SELECT payload_json FROM lane_review_events WHERE event_key = ?",
            (key,),
        ).fetchone()
        if existing is not None:
            if existing == (payload,):
                return False
            raise LaneReviewConflictError
        identity: tuple[str] | None = self._connection.execute(
            """SELECT payload_json FROM lane_review_events
            WHERE snapshot_key = ? AND experiment_scope_key = ?
            AND reviewer_version = ?""",
            (
                event.snapshot_key,
                event.experiment_scope_key,
                event.reviewer_version,
            ),
        ).fetchone()
        if identity is not None:
            raise LaneReviewConflictError
        try:
            _ = self._connection.execute(
                "INSERT INTO lane_review_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    event.lane_id.value,
                    event.session_date.isoformat(),
                    event.snapshot_key,
                    event.experiment_scope_key,
                    event.reviewer_version,
                    payload,
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise LaneReviewConflictError from error
        return True

    def _require_active(self) -> None:
        if not self._active:
            raise InactiveLaneReviewWriterError

    def _close(self) -> None:
        if self._active:
            self._active = False
            self._connection.close()


def _prepare_writer_connection(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    _ = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    current = 0 if version is None else version[0]
    if current == 0:
        objects = tuple(
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        )
        if objects:
            raise UnsupportedLaneReviewSchemaError
        connection.executescript(CREATE_LANE_REVIEW_SCHEMA)
        _ = connection.execute(f"PRAGMA user_version = {LANE_REVIEW_SCHEMA_VERSION}")
        connection.commit()
        return
    _require_current_schema(connection)


def _require_current_schema(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version != (LANE_REVIEW_SCHEMA_VERSION,):
        raise UnsupportedLaneReviewSchemaError
