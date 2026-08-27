from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, final, override

from pydantic import ValidationError

from trading_agent.kr_virtual_position_models import (
    KrVirtualPositionEvent,
    canonical_kr_virtual_position_event_json,
    validate_virtual_position_chains,
)
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_private_directory_query_only,
)
from trading_agent.sqlite_uri import sqlite_read_only_uri

_VERSION: Final = 1
_STATEMENTS: Final = (
    "CREATE TABLE kr_virtual_position_events (event_id TEXT PRIMARY KEY, position_id TEXT NOT NULL, "
    "recommendation_id TEXT NOT NULL, task_id TEXT NOT NULL, sequence INTEGER NOT NULL, "
    "previous_event_id TEXT, state TEXT NOT NULL, occurred_at TEXT NOT NULL, payload_sha256 TEXT NOT NULL, "
    "payload_json TEXT NOT NULL, UNIQUE(position_id,sequence))",
    "CREATE INDEX kr_virtual_position_events_by_position ON kr_virtual_position_events(position_id,sequence)",
    "CREATE INDEX kr_virtual_position_events_by_task ON kr_virtual_position_events(task_id,position_id,sequence)",
    "CREATE TRIGGER kr_virtual_position_events_no_update BEFORE UPDATE ON kr_virtual_position_events "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "CREATE TRIGGER kr_virtual_position_events_no_delete BEFORE DELETE ON kr_virtual_position_events "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
)
_EXPECTED: Final = {
    (kind, name): " ".join(statement.split())
    for kind, name, statement in (
        ("table", "kr_virtual_position_events", _STATEMENTS[0]),
        ("index", "kr_virtual_position_events_by_position", _STATEMENTS[1]),
        ("index", "kr_virtual_position_events_by_task", _STATEMENTS[2]),
        ("trigger", "kr_virtual_position_events_no_update", _STATEMENTS[3]),
        ("trigger", "kr_virtual_position_events_no_delete", _STATEMENTS[4]),
    )
}
type _Row = tuple[str, str, str, str, int, str | None, str, str, str, str]


class InvalidKrVirtualPositionStoreError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR virtual position store is invalid"


@final
class KrVirtualPositionStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def append(self, event: KrVirtualPositionEvent) -> bool:
        try:
            trusted = KrVirtualPositionEvent.model_validate(event.model_dump(mode="python"))
            payload = canonical_kr_virtual_position_event_json(trusted)
            with _open_database(self.path, write=True) as connection:
                _prepare(connection)
                return _append(connection, trusted, payload)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrVirtualPositionStoreError from None

    def events(self, position_id: str) -> tuple[KrVirtualPositionEvent, ...]:
        _require_id(position_id)
        return tuple(event for event in self.all_events() if event.position_id == position_id)

    def open_positions(self, task_id: str | None = None) -> tuple[KrVirtualPositionEvent, ...]:
        if task_id is not None:
            _require_id(task_id)
        latest: dict[str, KrVirtualPositionEvent] = {}
        for event in self.all_events():
            latest[event.position_id] = event
        return tuple(
            event for event in latest.values() if not event.terminal and (task_id is None or event.task_id == task_id)
        )

    def all_events(self) -> tuple[KrVirtualPositionEvent, ...]:
        if self.path.is_symlink():
            raise InvalidKrVirtualPositionStoreError
        if not self.path.exists():
            return ()
        try:
            with _open_database(self.path, write=False) as connection:
                connection.execute("PRAGMA query_only=ON")
                _require_schema(connection)
                rows = connection.execute(
                    "SELECT event_id,position_id,recommendation_id,task_id,sequence,previous_event_id,"
                    "state,occurred_at,payload_sha256,payload_json FROM kr_virtual_position_events ORDER BY rowid"
                ).fetchall()
            events = tuple(_decode(row) for row in rows)
            validate_virtual_position_chains(events)
            return events
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrVirtualPositionStoreError from None


def _append(connection: sqlite3.Connection, event: KrVirtualPositionEvent, payload: str) -> bool:
    row = _row(event, payload)
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT event_id,position_id,recommendation_id,task_id,sequence,previous_event_id,state,"
            "occurred_at,payload_sha256,payload_json FROM kr_virtual_position_events WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
        if existing is not None:
            if existing == row and _decode(existing) == event:
                connection.commit()
                return False
            raise InvalidKrVirtualPositionStoreError
        tail = connection.execute(
            "SELECT event_id,sequence FROM kr_virtual_position_events WHERE position_id=? "
            "ORDER BY sequence DESC LIMIT 1",
            (event.position_id,),
        ).fetchone()
        expected_id = None if tail is None else str(tail[0])
        expected_sequence = 1 if tail is None else int(tail[1]) + 1
        if event.previous_event_id != expected_id or event.sequence != expected_sequence:
            raise InvalidKrVirtualPositionStoreError
        connection.execute("INSERT INTO kr_virtual_position_events VALUES (?,?,?,?,?,?,?,?,?,?)", row)
        connection.commit()
        return True
    except (InvalidKrVirtualPositionStoreError, sqlite3.Error):
        connection.rollback()
        raise


def _row(event: KrVirtualPositionEvent, payload: str) -> _Row:
    digest = hashlib.sha256(payload.encode("ascii")).hexdigest()
    occurred = str(event.model_dump(mode="json")["occurred_at"])
    return (
        event.event_id,
        event.position_id,
        event.recommendation_id,
        event.task_id,
        event.sequence,
        event.previous_event_id,
        event.state.value,
        occurred,
        digest,
        payload,
    )


def _decode(row: _Row) -> KrVirtualPositionEvent:
    event = KrVirtualPositionEvent.model_validate_json(row[9])
    payload = canonical_kr_virtual_position_event_json(event)
    if row != _row(event, payload):
        raise InvalidKrVirtualPositionStoreError
    return event


@contextmanager
def _open_database(path: Path, *, write: bool) -> Iterator[sqlite3.Connection]:
    parent: int | None = None
    descriptor: int | None = None
    connection: sqlite3.Connection | None = None
    try:
        parent = open_private_parent(path.parent, create=write)
        require_private_directory_query_only(parent)
        descriptor = _open_file(parent, path.name, write=write)
        connection = sqlite3.connect(path if write else sqlite_read_only_uri(path), uri=not write, timeout=5.0)
        _require_identity(parent, path.name, descriptor)
        yield connection
    finally:
        try:
            if parent is not None and descriptor is not None:
                _require_identity(parent, path.name, descriptor)
        finally:
            if connection is not None:
                connection.close()
            if descriptor is not None:
                os.close(descriptor)
            if parent is not None:
                os.close(parent)


def _open_file(parent: int, name: str, *, write: bool) -> int:
    flags = os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_RDWR if write else os.O_RDONLY)
    try:
        descriptor = os.open(name, flags | (os.O_CREAT | os.O_EXCL if write else 0), 0o600, dir_fd=parent)
    except FileExistsError:
        descriptor = os.open(name, flags, dir_fd=parent)
    if not _private_file(os.fstat(descriptor)):
        os.close(descriptor)
        raise InvalidKrVirtualPositionStoreError
    return descriptor


def _require_identity(parent: int, name: str, descriptor: int) -> None:
    named, opened = os.stat(name, dir_fd=parent, follow_symlinks=False), os.fstat(descriptor)
    if (
        not _private_file(named)
        or not _private_file(opened)
        or (named.st_dev, named.st_ino)
        != (
            opened.st_dev,
            opened.st_ino,
        )
    ):
        raise InvalidKrVirtualPositionStoreError


def _private_file(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def _prepare(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA trusted_schema=OFF")
    if connection.execute("PRAGMA user_version").fetchone() == (0,):
        for statement in _STATEMENTS:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version={_VERSION}")
        connection.commit()
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    objects = {
        (str(row[0]), str(row[1])): " ".join(str(row[2]).split())
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    }
    if connection.execute("PRAGMA user_version").fetchone() != (_VERSION,) or objects != _EXPECTED:
        raise InvalidKrVirtualPositionStoreError


def _require_id(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise InvalidKrVirtualPositionStoreError


__all__ = ("InvalidKrVirtualPositionStoreError", "KrVirtualPositionStore")
