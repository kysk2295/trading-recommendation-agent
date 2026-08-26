from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from trading_agent._autonomous_memory_store_sqlite import (
    AutonomousMemoryConflictError,
    AutonomousMemoryStoreError,
    DatabaseIdentity,
    InvalidAutonomousMemoryStoreError,
    flush_generation,
    open_database,
    open_private_parent,
    reader_connection,
    reconcile_generation,
    writer_connection,
    writer_lease,
)
from trading_agent.autonomous_memory_models import AutonomousMemoryRecord, AutonomousMemoryScope
from trading_agent.private_directory_identity import require_private_directory


class AutonomousMemoryStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        absolute = Path(os.path.abspath(path.expanduser()))
        self.path = absolute.parent.resolve(strict=False) / absolute.name

    @contextmanager
    def writer(self) -> Iterator[AutonomousMemoryWriter]:
        parent = -1
        descriptor = -1
        try:
            parent = open_private_parent(self.path.parent, create=True)
            require_private_directory(parent)
            with writer_lease(self.path, parent):
                descriptor = open_database(parent, self.path.name, create=True, write=True)
                identity = DatabaseIdentity(parent, self.path.name, descriptor, self.path)
                descriptor = -1
                try:
                    with writer_connection(identity) as connection:
                        writer = AutonomousMemoryWriter(
                            connection,
                            lambda: flush_generation(identity, connection),
                            lambda: reconcile_generation(identity, connection),
                        )
                        try:
                            yield writer
                        finally:
                            writer.close()
                finally:
                    os.close(identity.descriptor)
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            if isinstance(error, AutonomousMemoryStoreError):
                raise
            raise InvalidAutonomousMemoryStoreError(reason="database_write_failed") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if parent >= 0:
                os.close(parent)

    def reader(self) -> AutonomousMemoryReader:
        return AutonomousMemoryReader(self.path)


class AutonomousMemoryWriter:
    __slots__ = ("_active", "_connection", "_flush", "_reconcile")

    def __init__(
        self, connection: sqlite3.Connection, flush: Callable[[], None], reconcile: Callable[[], None]
    ) -> None:
        self._active = True
        self._connection = connection
        self._flush = flush
        self._reconcile = reconcile

    def append(self, record: AutonomousMemoryRecord) -> bool:
        self._require_active()
        row = _memory_row(record)
        _ = self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._connection.execute(
                "SELECT memory_id,memory_key,version,scope,recorded_at,payload_sha256,payload_json "
                "FROM autonomous_memories WHERE memory_id=? OR (memory_key=? AND version=?)",
                (record.memory_id, record.memory_key, record.version),
            ).fetchone()
            if existing is not None:
                if tuple(existing) == row:
                    self._connection.rollback()
                    return False
                raise AutonomousMemoryConflictError(reason="memory_replay_conflict")
            history = _history(self._connection, record.memory_key)
            if not history and record.version != 1:
                raise AutonomousMemoryConflictError(reason="memory_version_invalid")
            if history:
                previous = history[-1]
                if record.version != previous.version + 1:
                    raise AutonomousMemoryConflictError(reason="memory_version_invalid")
                if record.scope != previous.scope:
                    raise AutonomousMemoryConflictError(reason="memory_scope_invalid")
                if record.recorded_at < previous.recorded_at:
                    raise AutonomousMemoryConflictError(reason="memory_timestamp_invalid")
            _ = self._connection.execute("INSERT INTO autonomous_memories VALUES (?,?,?,?,?,?,?)", row)
            self._connection.commit()
            self._flush()
            self._reconcile()
            return True
        except AutonomousMemoryStoreError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise InvalidAutonomousMemoryStoreError(reason="memory_insert_failed") from error

    def close(self) -> None:
        self._active = False

    def _require_active(self) -> None:
        if not self._active:
            raise InvalidAutonomousMemoryStoreError(reason="writer_closed")


class AutonomousMemoryReader:
    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path

    def latest(self, memory_key: str) -> AutonomousMemoryRecord | None:
        records = self.history(memory_key)
        return records[-1] if records else None

    def history(self, memory_key: str) -> tuple[AutonomousMemoryRecord, ...]:
        return self._records("WHERE memory_key=? ORDER BY version", (memory_key,))

    def search(
        self, scope: AutonomousMemoryScope, subject_refs: tuple[str, ...], *, limit: int
    ) -> tuple[AutonomousMemoryRecord, ...]:
        if type(limit) is not int or not 1 <= limit <= 32:
            raise InvalidAutonomousMemoryStoreError(reason="search_limit_invalid")
        if (
            not subject_refs
            or any(not item for item in subject_refs)
            or tuple(sorted(subject_refs)) != subject_refs
            or len(set(subject_refs)) != len(subject_refs)
        ):
            raise InvalidAutonomousMemoryStoreError(reason="search_subject_refs_invalid")
        matches = tuple(
            record
            for record in self._records("WHERE scope=?", (scope.value,))
            if set(record.subject_refs) & set(subject_refs)
        )
        return tuple(
            sorted(matches, key=lambda record: (-record.recorded_at.timestamp(), -record.version, record.memory_id))[
                :limit
            ]
        )

    def _records(self, clause: str, parameters: tuple[str, ...]) -> tuple[AutonomousMemoryRecord, ...]:
        try:
            with reader_connection(self._path) as connection:
                rows = connection.execute(
                    "SELECT memory_id,memory_key,version,scope,recorded_at,payload_sha256,payload_json "
                    "FROM autonomous_memories " + clause,
                    parameters,
                ).fetchall()
        except FileNotFoundError:
            return ()
        return tuple(_record_from_row(row) for row in rows)


def _storage_payload(record: AutonomousMemoryRecord) -> str:
    return json.dumps(record.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _memory_row(record: AutonomousMemoryRecord) -> tuple[str, str, int, str, str, str, str]:
    payload = _storage_payload(record)
    return (
        record.memory_id,
        record.memory_key,
        record.version,
        record.scope.value,
        record.recorded_at.isoformat(),
        hashlib.sha256(payload.encode()).hexdigest(),
        payload,
    )


def _history(connection: sqlite3.Connection, memory_key: str) -> tuple[AutonomousMemoryRecord, ...]:
    rows = connection.execute(
        "SELECT memory_id,memory_key,version,scope,recorded_at,payload_sha256,payload_json "
        "FROM autonomous_memories WHERE memory_key=? ORDER BY version",
        (memory_key,),
    ).fetchall()
    return tuple(_record_from_row(row) for row in rows)


def _record_from_row(row: tuple[str, str, int, str, str, str, str]) -> AutonomousMemoryRecord:
    try:
        record = AutonomousMemoryRecord.model_validate_json(row[-1])
    except ValueError as error:
        raise InvalidAutonomousMemoryStoreError(reason="memory_payload_invalid") from error
    if _memory_row(record) != row:
        raise InvalidAutonomousMemoryStoreError(reason="memory_payload_invalid")
    return record


__all__ = (
    "AutonomousMemoryConflictError",
    "AutonomousMemoryReader",
    "AutonomousMemoryStore",
    "AutonomousMemoryStoreError",
    "AutonomousMemoryWriter",
    "InvalidAutonomousMemoryStoreError",
)
