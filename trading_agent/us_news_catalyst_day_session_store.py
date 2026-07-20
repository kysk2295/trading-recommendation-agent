from __future__ import annotations

import fcntl
import hashlib
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, final, override

from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.sqlite_uri import sqlite_read_only_uri
from trading_agent.us_news_catalyst_day_session_audit import (
    InvalidUsNewsCatalystDaySessionAuditError,
    UsNewsCatalystDaySessionEvent,
    us_news_catalyst_day_session_event_bytes,
    us_news_catalyst_day_session_event_from_bytes,
)

_SCHEMA: Final = """
CREATE TABLE us_news_catalyst_day_session_events (
  generation INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_id TEXT NOT NULL UNIQUE,
  previous_event_id TEXT,
  payload_sha256 TEXT NOT NULL,
  payload BLOB NOT NULL,
  UNIQUE(session_id, sequence)
);
CREATE TRIGGER us_news_catalyst_day_session_events_no_update
BEFORE UPDATE ON us_news_catalyst_day_session_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER us_news_catalyst_day_session_events_no_delete
BEFORE DELETE ON us_news_catalyst_day_session_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_OBJECTS: Final = frozenset(
    {
        "us_news_catalyst_day_session_events",
        "us_news_catalyst_day_session_events_no_delete",
        "us_news_catalyst_day_session_events_no_update",
    }
)


class InvalidUsNewsCatalystDaySessionStoreError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst day session store is invalid"


class UsNewsCatalystDaySessionWriterLeaseUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst day session single writer lease is unavailable"


@final
class UsNewsCatalystDaySessionStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    @contextmanager
    def writer(self) -> Iterator[UsNewsCatalystDaySessionWriter]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        lock_path = Path(f"{self.path}.writer.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise UsNewsCatalystDaySessionWriterLeaseUnavailableError from error
            connection = self._connection(write=True)
            writer = UsNewsCatalystDaySessionWriter(connection)
            try:
                yield writer
            finally:
                writer.close()
                connection.close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def events(self, session_id: str) -> tuple[UsNewsCatalystDaySessionEvent, ...]:
        if not self.path.exists():
            return ()
        connection = self._connection(write=False)
        try:
            rows = connection.execute(
                "SELECT event_id,previous_event_id,payload_sha256,payload "
                "FROM us_news_catalyst_day_session_events WHERE session_id=? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        finally:
            connection.close()
        return _verified_events(session_id, rows)

    def _connection(self, *, write: bool) -> sqlite3.Connection:
        try:
            if self.path.is_symlink():
                raise InvalidUsNewsCatalystDaySessionStoreError
            if write:
                existed = self.path.exists()
                if existed:
                    _require_private(self.path)
                connection = sqlite3.connect(self.path, timeout=0.0)
                if not existed:
                    os.chmod(self.path, 0o600)
                if connection.execute("PRAGMA user_version").fetchone() == (0,):
                    connection.executescript(_SCHEMA)
                    _ = connection.execute("PRAGMA user_version=1")
                    connection.commit()
            else:
                _require_private(self.path)
                connection = sqlite3.connect(sqlite_read_only_uri(self.path), uri=True)
                _ = connection.execute("PRAGMA query_only=ON")
            _require_schema(connection)
            return connection
        except (OSError, sqlite3.Error, ValueError):
            raise InvalidUsNewsCatalystDaySessionStoreError from None


@final
class UsNewsCatalystDaySessionWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def append(self, event: UsNewsCatalystDaySessionEvent) -> bool:
        if not self._active:
            raise InvalidUsNewsCatalystDaySessionStoreError
        try:
            payload = us_news_catalyst_day_session_event_bytes(event)
            row = (event.session_id, event.sequence, event.event_id, event.previous_event_id,
                   hashlib.sha256(payload).hexdigest(), payload)
            existing = self._connection.execute(
                "SELECT session_id,sequence,event_id,previous_event_id,payload_sha256,payload "
                "FROM us_news_catalyst_day_session_events WHERE session_id=? AND sequence=?",
                (event.session_id, event.sequence),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != row:
                    raise InvalidUsNewsCatalystDaySessionStoreError
                return False
            previous = self._connection.execute(
                "SELECT sequence,event_id FROM us_news_catalyst_day_session_events "
                "WHERE session_id=? ORDER BY sequence DESC LIMIT 1", (event.session_id,)
            ).fetchone()
            expected = (1, None) if previous is None else (previous[0] + 1, previous[1])
            if (event.sequence, event.previous_event_id) != expected:
                raise InvalidUsNewsCatalystDaySessionStoreError
            _ = self._connection.execute(
                "INSERT INTO us_news_catalyst_day_session_events "
                "(session_id,sequence,event_id,previous_event_id,payload_sha256,payload) "
                "VALUES (?,?,?,?,?,?)", row,
            )
            self._connection.commit()
            return True
        except (InvalidUsNewsCatalystDaySessionAuditError, sqlite3.Error, ValueError):
            raise InvalidUsNewsCatalystDaySessionStoreError from None

    def events(self, session_id: str) -> tuple[UsNewsCatalystDaySessionEvent, ...]:
        if not self._active:
            raise InvalidUsNewsCatalystDaySessionStoreError
        rows = self._connection.execute(
            "SELECT event_id,previous_event_id,payload_sha256,payload "
            "FROM us_news_catalyst_day_session_events WHERE session_id=? ORDER BY sequence",
            (session_id,),
        ).fetchall()
        return _verified_events(session_id, rows)

    def close(self) -> None:
        self._active = False


def _verified_events(
    session_id: str,
    rows: list[tuple[str, str | None, str, bytes]],
) -> tuple[UsNewsCatalystDaySessionEvent, ...]:
    events: list[UsNewsCatalystDaySessionEvent] = []
    previous: str | None = None
    for sequence, row in enumerate(rows, start=1):
        if hashlib.sha256(row[3]).hexdigest() != row[2]:
            raise InvalidUsNewsCatalystDaySessionStoreError
        event = us_news_catalyst_day_session_event_from_bytes(row[3])
        if (event.session_id, event.sequence, event.event_id, event.previous_event_id) != (
            session_id, sequence, row[0], previous,
        ) or row[1] != previous:
            raise InvalidUsNewsCatalystDaySessionStoreError
        events.append(event)
        previous = event.event_id
    return tuple(events)


def _require_schema(connection: sqlite3.Connection) -> None:
    objects = frozenset(
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'"
        )
    )
    if connection.execute("PRAGMA user_version").fetchone() != (1,) or objects != _OBJECTS:
        raise InvalidUsNewsCatalystDaySessionStoreError


def _require_private(path: Path) -> None:
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1):
        raise InvalidUsNewsCatalystDaySessionStoreError
