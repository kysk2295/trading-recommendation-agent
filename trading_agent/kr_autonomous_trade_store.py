from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, final, override

from pydantic import TypeAdapter, ValidationError

from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousTradeEvent,
    canonical_kr_autonomous_trade_event_json,
)
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_private_directory_query_only,
)
from trading_agent.sqlite_uri import sqlite_read_only_uri

_VERSION: Final = 1
_EVENT_ADAPTER: Final = TypeAdapter(KrAutonomousTradeEvent)
_STATEMENTS: Final = (
    "CREATE TABLE kr_autonomous_trade_events (event_id TEXT PRIMARY KEY, previous_event_id TEXT, "
    "task_id TEXT NOT NULL, outcome TEXT NOT NULL, timestamp TEXT NOT NULL, payload_sha256 TEXT NOT NULL, "
    "payload_json TEXT NOT NULL)",
    "CREATE INDEX kr_autonomous_trade_events_history ON kr_autonomous_trade_events(timestamp,event_id)",
    "CREATE TRIGGER kr_autonomous_trade_events_no_update BEFORE UPDATE ON kr_autonomous_trade_events "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "CREATE TRIGGER kr_autonomous_trade_events_no_delete BEFORE DELETE ON kr_autonomous_trade_events "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
)
_EXPECTED: Final = {
    ("table", "kr_autonomous_trade_events"): " ".join(_STATEMENTS[0].split()),
    ("index", "kr_autonomous_trade_events_history"): " ".join(_STATEMENTS[1].split()),
    ("trigger", "kr_autonomous_trade_events_no_update"): " ".join(_STATEMENTS[2].split()),
    ("trigger", "kr_autonomous_trade_events_no_delete"): " ".join(_STATEMENTS[3].split()),
}
type _Row = tuple[str, str | None, str, str, str, str, str]


class InvalidKrAutonomousTradeStoreError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR autonomous virtual trade store is invalid"


@final
class KrAutonomousTradeStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def append(self, event: KrAutonomousTradeEvent) -> bool:
        try:
            trusted = _EVENT_ADAPTER.validate_python(event.model_dump(mode="python"))
            payload = canonical_kr_autonomous_trade_event_json(trusted)
            with _open_writer(self.path) as connection:
                return _append(connection, trusted, payload)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrAutonomousTradeStoreError from None

    def events(self) -> tuple[KrAutonomousTradeEvent, ...]:
        if self.path.is_symlink():
            raise InvalidKrAutonomousTradeStoreError
        if not self.path.exists():
            return ()
        try:
            with _open_reader(self.path) as connection:
                rows = connection.execute(
                    "SELECT event_id,previous_event_id,task_id,outcome,timestamp,payload_sha256,payload_json "
                    "FROM kr_autonomous_trade_events ORDER BY rowid"
                ).fetchall()
                events = tuple(_decode(row) for row in rows)
                _require_chain(events)
                return events
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrAutonomousTradeStoreError from None

    def event(self, event_id: str) -> KrAutonomousTradeEvent | None:
        if len(event_id) != 64 or any(character not in "0123456789abcdef" for character in event_id):
            raise InvalidKrAutonomousTradeStoreError
        matches = tuple(event for event in self.events() if event.event_id == event_id)
        if len(matches) > 1:
            raise InvalidKrAutonomousTradeStoreError
        return matches[0] if matches else None


def _append(connection: sqlite3.Connection, event: KrAutonomousTradeEvent, payload: str) -> bool:
    digest = hashlib.sha256(payload.encode("ascii")).hexdigest()
    row = _row(event, payload, digest)
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT event_id,previous_event_id,task_id,outcome,timestamp,payload_sha256,payload_json "
            "FROM kr_autonomous_trade_events WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
        if existing is not None:
            if existing == row and _decode(existing) == event:
                connection.commit()
                return False
            raise InvalidKrAutonomousTradeStoreError
        tail = connection.execute(
            "SELECT event_id FROM kr_autonomous_trade_events ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        expected = None if tail is None else str(tail[0])
        if event.previous_event_id != expected:
            raise InvalidKrAutonomousTradeStoreError
        connection.execute("INSERT INTO kr_autonomous_trade_events VALUES (?,?,?,?,?,?,?)", row)
        connection.commit()
        return True
    except (InvalidKrAutonomousTradeStoreError, sqlite3.Error):
        connection.rollback()
        raise


def _decode(row: _Row) -> KrAutonomousTradeEvent:
    event = _EVENT_ADAPTER.validate_json(row[6])
    payload = canonical_kr_autonomous_trade_event_json(event)
    if row != _row(event, payload, hashlib.sha256(payload.encode("ascii")).hexdigest()):
        raise InvalidKrAutonomousTradeStoreError
    return event


def _row(event: KrAutonomousTradeEvent, payload: str, digest: str) -> _Row:
    timestamp = str(event.model_dump(mode="json")["timestamp"])
    return (event.event_id, event.previous_event_id, event.task_id, event.outcome.value, timestamp, digest, payload)


def _require_chain(events: tuple[KrAutonomousTradeEvent, ...]) -> None:
    previous: str | None = None
    for event in events:
        if event.previous_event_id != previous:
            raise InvalidKrAutonomousTradeStoreError
        previous = event.event_id


@contextmanager
def _open_writer(path: Path) -> Iterator[sqlite3.Connection]:
    with _open_database(path, write=True) as connection:
        _prepare(connection)
        yield connection


@contextmanager
def _open_reader(path: Path) -> Iterator[sqlite3.Connection]:
    with _open_database(path, write=False) as connection:
        connection.execute("PRAGMA query_only=ON")
        _require_schema(connection)
        yield connection


@contextmanager
def _open_database(path: Path, *, write: bool) -> Iterator[sqlite3.Connection]:
    parent: int | None = None
    descriptor: int | None = None
    connection: sqlite3.Connection | None = None
    try:
        parent = open_private_parent(path.parent, create=write)
        require_private_directory_query_only(parent)
        descriptor = _open_file(parent, path.name, write=write)
        target = path if write else sqlite_read_only_uri(path)
        connection = sqlite3.connect(target, uri=not write, timeout=5.0)
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
        raise InvalidKrAutonomousTradeStoreError
    return descriptor


def _require_identity(parent: int, name: str, descriptor: int) -> None:
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (
        not _private_file(named)
        or not _private_file(opened)
        or (named.st_dev, named.st_ino)
        != (
            opened.st_dev,
            opened.st_ino,
        )
    ):
        raise InvalidKrAutonomousTradeStoreError


def _private_file(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def _prepare(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        if connection.execute("PRAGMA user_version").fetchone() == (0,):
            for statement in _STATEMENTS:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version={_VERSION}")
        _require_schema(connection)
        connection.commit()
    except (InvalidKrAutonomousTradeStoreError, sqlite3.Error):
        connection.rollback()
        raise


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_VERSION,):
        raise InvalidKrAutonomousTradeStoreError
    objects = {
        (str(row[0]), str(row[1])): " ".join(str(row[2]).split())
        for row in connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    if objects != _EXPECTED:
        raise InvalidKrAutonomousTradeStoreError


__all__ = ("InvalidKrAutonomousTradeStoreError", "KrAutonomousTradeStore")
