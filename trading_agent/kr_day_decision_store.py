from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Final, final, override

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_decision_models import KrDayDecisionEvent
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory_query_only,
)
from trading_agent.sqlite_uri import sqlite_read_only_uri

_SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE kr_day_decision_events (
  event_id TEXT PRIMARY KEY,
  session_date TEXT NOT NULL,
  capsule_id TEXT NOT NULL,
  hypothesis_version_id TEXT NOT NULL,
  opportunity_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  completed_bar_at TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(session_date, capsule_id, opportunity_id, completed_bar_at, status)
);
CREATE INDEX kr_day_decision_events_by_identity
ON kr_day_decision_events(capsule_id, opportunity_id, session_date, completed_bar_at);
CREATE TRIGGER kr_day_decision_events_no_update
BEFORE UPDATE ON kr_day_decision_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_day_decision_events_no_delete
BEFORE DELETE ON kr_day_decision_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
type _EventRow = tuple[str, str, str, str, str, str, str, str, str, str]
type _SchemaDefinition = tuple[str, str]


class InvalidKrDayDecisionStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day pre-entry decision store is invalid"


@final
class KrDayDecisionStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def events(self) -> tuple[KrDayDecisionEvent, ...]:
        if self.path.is_symlink():
            raise InvalidKrDayDecisionStoreError
        if not self.path.exists():
            return ()
        try:
            _require_private_file(self.path)
            with closing(sqlite3.connect(sqlite_read_only_uri(self.path), uri=True)) as connection:
                _ = connection.execute("PRAGMA query_only = ON")
                _require_schema(connection)
                rows: list[_EventRow] = connection.execute(
                    "SELECT event_id,session_date,capsule_id,hypothesis_version_id,"
                    "opportunity_id,symbol,completed_bar_at,status,payload_sha256,payload_json "
                    "FROM kr_day_decision_events ORDER BY rowid"
                ).fetchall()
            return tuple(_event_from_row(row) for row in rows)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrDayDecisionStoreError from None

    def latest(
        self,
        capsule_id: str,
        opportunity_id: str,
        session_date: dt.date,
    ) -> KrDayDecisionEvent | None:
        matches = tuple(
            event
            for event in self.events()
            if event.capsule_id == capsule_id
            and event.opportunity_id == opportunity_id
            and event.session_date == session_date
        )
        return matches[-1] if matches else None

    def event(self, event_id: str) -> KrDayDecisionEvent | None:
        matches = tuple(item for item in self.events() if item.event_id == event_id)
        if len(matches) > 1:
            raise InvalidKrDayDecisionStoreError
        return matches[0] if matches else None

    def append(self, event: KrDayDecisionEvent) -> bool:
        parent = -1
        try:
            event = KrDayDecisionEvent.model_validate(event.model_dump(mode="python"))
            _ = self.events()
            if self.path.is_symlink():
                raise InvalidKrDayDecisionStoreError
            parent = open_private_parent(self.path.parent, create=True)
            require_private_directory_query_only(parent)
            require_open_directory_path(self.path.parent, parent)
            with closing(sqlite3.connect(self.path, timeout=0.0)) as connection:
                _prepare(connection)
                os.chmod(self.path, 0o600)
                connection.execute("BEGIN IMMEDIATE")
                row = _row(event)
                existing: _EventRow | None = connection.execute(
                    "SELECT event_id,session_date,capsule_id,hypothesis_version_id,"
                    "opportunity_id,symbol,completed_bar_at,status,payload_sha256,payload_json "
                    "FROM kr_day_decision_events WHERE session_date=? AND capsule_id=? "
                    "AND opportunity_id=? AND completed_bar_at=? AND status=?",
                    (
                        event.session_date.isoformat(),
                        event.capsule_id,
                        event.opportunity_id,
                        event.completed_bar_at.isoformat(),
                        event.status.value,
                    ),
                ).fetchone()
                if existing is not None:
                    if existing != row:
                        raise InvalidKrDayDecisionStoreError
                    connection.rollback()
                    return False
                _require_lineage(connection, event)
                _ = connection.execute(
                    "INSERT INTO kr_day_decision_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                    row,
                )
                connection.commit()
            require_open_directory_path(self.path.parent, parent)
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrDayDecisionStoreError from None
        finally:
            if parent >= 0:
                os.close(parent)


def _row(event: KrDayDecisionEvent) -> _EventRow:
    payload = canonical_experiment_ledger_json(event)
    return (
        event.event_id,
        event.session_date.isoformat(),
        event.capsule_id,
        event.hypothesis_version_id,
        event.opportunity_id,
        event.symbol,
        event.completed_bar_at.isoformat(),
        event.status.value,
        hashlib.sha256(payload.encode()).hexdigest(),
        payload,
    )


def _event_from_row(row: _EventRow) -> KrDayDecisionEvent:
    event = KrDayDecisionEvent.model_validate_json(row[-1])
    if row != _row(event):
        raise InvalidKrDayDecisionStoreError
    return event


def _require_lineage(connection: sqlite3.Connection, event: KrDayDecisionEvent) -> None:
    latest: tuple[str] | None = connection.execute(
        "SELECT event_id FROM kr_day_decision_events WHERE capsule_id=? AND opportunity_id=? "
        "AND session_date=? ORDER BY rowid DESC LIMIT 1",
        (event.capsule_id, event.opportunity_id, event.session_date.isoformat()),
    ).fetchone()
    expected = latest[0] if latest is not None else None
    if event.previous_event_id != expected:
        raise InvalidKrDayDecisionStoreError


def _prepare(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() == (0,):
        connection.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={_SCHEMA_VERSION};COMMIT;")
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
        raise InvalidKrDayDecisionStoreError
    with closing(sqlite3.connect(":memory:")) as expected_connection:
        expected_connection.executescript(_SCHEMA)
        expected = _schema_definitions(expected_connection)
    if _schema_definitions(connection) != expected:
        raise InvalidKrDayDecisionStoreError


def _schema_definitions(connection: sqlite3.Connection) -> dict[str, _SchemaDefinition]:
    rows: list[tuple[str, str, str | None]] = connection.execute(
        "SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return {
        name: (object_type, "" if sql is None else " ".join(sql.split()))
        for name, object_type, sql in rows
    }


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKrDayDecisionStoreError


__all__ = ("InvalidKrDayDecisionStoreError", "KrDayDecisionStore")
