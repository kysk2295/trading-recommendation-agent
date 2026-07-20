from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Final, final

from trading_agent.kr_theme_day_session_audit import (
    InvalidKrThemeDaySessionAuditError,
    KrThemeDaySessionPhaseEvent,
    kr_theme_day_session_phase_event_bytes,
    kr_theme_day_session_phase_event_from_bytes,
)
from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.sqlite_uri import sqlite_read_only_uri

_SCHEMA: Final = """
CREATE TABLE kr_theme_day_session_events (
  generation INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_id TEXT NOT NULL UNIQUE,
  previous_event_id TEXT,
  exit_code INTEGER NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload BLOB NOT NULL,
  UNIQUE(session_id, sequence)
);
CREATE TRIGGER kr_theme_day_session_events_no_update
BEFORE UPDATE ON kr_theme_day_session_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_theme_day_session_events_no_delete
BEFORE DELETE ON kr_theme_day_session_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_OBJECTS: Final = frozenset(
    {
        "kr_theme_day_session_events",
        "kr_theme_day_session_events_no_delete",
        "kr_theme_day_session_events_no_update",
    }
)


@final
class KrThemeDaySessionAuditStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def append(self, event: KrThemeDaySessionPhaseEvent) -> bool:
        try:
            payload = kr_theme_day_session_phase_event_bytes(event)
            row = (
                event.session_id,
                event.sequence,
                event.event_id,
                event.previous_event_id,
                event.exit_code,
                hashlib.sha256(payload).hexdigest(),
                payload,
            )
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT session_id,sequence,event_id,previous_event_id,exit_code,payload_sha256,payload "
                    "FROM kr_theme_day_session_events WHERE session_id=? AND sequence=?",
                    (event.session_id, event.sequence),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise InvalidKrThemeDaySessionAuditError
                    return False
                previous = connection.execute(
                    "SELECT sequence,event_id FROM kr_theme_day_session_events WHERE session_id=? "
                    "ORDER BY sequence DESC LIMIT 1",
                    (event.session_id,),
                ).fetchone()
                expected_sequence = 1 if previous is None else previous[0] + 1
                expected_previous = None if previous is None else previous[1]
                if event.sequence != expected_sequence or event.previous_event_id != expected_previous:
                    raise InvalidKrThemeDaySessionAuditError
                _ = connection.execute(
                    "INSERT INTO kr_theme_day_session_events "
                    "(session_id,sequence,event_id,previous_event_id,exit_code,payload_sha256,payload) "
                    "VALUES (?,?,?,?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrThemeDaySessionAuditError from None

    def events(self, session_id: str) -> tuple[KrThemeDaySessionPhaseEvent, ...]:
        if not self.path.exists():
            return ()
        try:
            with closing(self._connection(write=False)) as connection:
                rows = connection.execute(
                    "SELECT event_id,previous_event_id,payload_sha256,payload "
                    "FROM kr_theme_day_session_events WHERE session_id=? ORDER BY sequence",
                    (session_id,),
                ).fetchall()
            events: list[KrThemeDaySessionPhaseEvent] = []
            previous: str | None = None
            for sequence, row in enumerate(rows, start=1):
                if hashlib.sha256(row[3]).hexdigest() != row[2]:
                    raise InvalidKrThemeDaySessionAuditError
                event = kr_theme_day_session_phase_event_from_bytes(row[3])
                if (
                    event.session_id != session_id
                    or event.sequence != sequence
                    or event.event_id != row[0]
                    or event.previous_event_id != row[1]
                    or event.previous_event_id != previous
                ):
                    raise InvalidKrThemeDaySessionAuditError
                events.append(event)
                previous = event.event_id
            return tuple(events)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrThemeDaySessionAuditError from None

    def _connection(self, *, write: bool) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise InvalidKrThemeDaySessionAuditError
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
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


def _require_schema(connection: sqlite3.Connection) -> None:
    objects = frozenset(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'"
        )
    )
    if connection.execute("PRAGMA user_version").fetchone() != (1,) or objects != _OBJECTS:
        raise InvalidKrThemeDaySessionAuditError


def _require_private(path: Path) -> None:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKrThemeDaySessionAuditError
