from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Final, final, override

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowEvent
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory_query_only,
)
from trading_agent.sqlite_uri import sqlite_read_only_uri

_SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE kr_day_capsule_shadow_events (
  event_id TEXT PRIMARY KEY,
  capsule_id TEXT NOT NULL,
  session_date TEXT NOT NULL,
  attempted_bar_cursor TEXT NOT NULL,
  evaluation_id TEXT NOT NULL UNIQUE,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(capsule_id, session_date, attempted_bar_cursor)
);
CREATE INDEX kr_day_capsule_shadow_events_by_capsule
ON kr_day_capsule_shadow_events(capsule_id, session_date, attempted_bar_cursor);
CREATE TRIGGER kr_day_capsule_shadow_events_no_update
BEFORE UPDATE ON kr_day_capsule_shadow_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_day_capsule_shadow_events_no_delete
BEFORE DELETE ON kr_day_capsule_shadow_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_OBJECTS: Final = frozenset(
    {
        "kr_day_capsule_shadow_events",
        "kr_day_capsule_shadow_events_by_capsule",
        "kr_day_capsule_shadow_events_no_update",
        "kr_day_capsule_shadow_events_no_delete",
    }
)
type _EventRow = tuple[str, str, str, str, str, str, str]


class InvalidKrDayCapsuleShadowStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day capsule shadow store is invalid"


@final
class KrDayCapsuleShadowStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def events(self) -> tuple[KrDayCapsuleShadowEvent, ...]:
        if self.path.is_symlink():
            raise InvalidKrDayCapsuleShadowStoreError
        if not self.path.exists():
            return ()
        try:
            _require_private_file(self.path)
            with closing(sqlite3.connect(sqlite_read_only_uri(self.path), uri=True)) as connection:
                _require_schema(connection)
                rows: list[_EventRow] = connection.execute(
                    "SELECT event_id,capsule_id,session_date,attempted_bar_cursor,"
                    "evaluation_id,payload_sha256,payload_json "
                    "FROM kr_day_capsule_shadow_events ORDER BY rowid"
                ).fetchall()
            return tuple(_event_from_row(row) for row in rows)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrDayCapsuleShadowStoreError from None

    def latest(self, capsule_id: str, session_date: str) -> KrDayCapsuleShadowEvent | None:
        matches = tuple(
            event
            for event in self.events()
            if event.capsule_id == capsule_id and event.session_date.isoformat() == session_date
        )
        return matches[-1] if matches else None

    def event_for_evaluation(self, evaluation_id: str) -> KrDayCapsuleShadowEvent | None:
        matches = tuple(event for event in self.events() if event.evaluation_id == evaluation_id)
        if len(matches) > 1:
            raise InvalidKrDayCapsuleShadowStoreError
        return matches[0] if matches else None

    def append(self, event: KrDayCapsuleShadowEvent) -> bool:
        parent = -1
        try:
            event = KrDayCapsuleShadowEvent.model_validate(event.model_dump(mode="python"))
            _ = self.events()
            if self.path.is_symlink():
                raise InvalidKrDayCapsuleShadowStoreError
            parent = open_private_parent(self.path.parent, create=True)
            require_private_directory_query_only(parent)
            require_open_directory_path(self.path.parent, parent)
            with closing(sqlite3.connect(self.path, timeout=0.0)) as connection:
                _prepare(connection)
                os.chmod(self.path, 0o600)
                connection.execute("BEGIN IMMEDIATE")
                row = _row(event)
                existing: _EventRow | None = connection.execute(
                    "SELECT event_id,capsule_id,session_date,attempted_bar_cursor,"
                    "evaluation_id,payload_sha256,payload_json "
                    "FROM kr_day_capsule_shadow_events WHERE evaluation_id=? OR "
                    "(capsule_id=? AND session_date=? AND attempted_bar_cursor=?)",
                    (
                        event.evaluation_id,
                        event.capsule_id,
                        event.session_date.isoformat(),
                        event.attempted_bar_cursor.isoformat(),
                    ),
                ).fetchone()
                if existing is not None:
                    if existing != row:
                        raise InvalidKrDayCapsuleShadowStoreError
                    connection.rollback()
                    return False
                _ = connection.execute(
                    "INSERT INTO kr_day_capsule_shadow_events VALUES (?,?,?,?,?,?,?)",
                    row,
                )
                connection.commit()
            require_open_directory_path(self.path.parent, parent)
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrDayCapsuleShadowStoreError from None
        finally:
            if parent >= 0:
                os.close(parent)


def _row(event: KrDayCapsuleShadowEvent) -> _EventRow:
    payload = canonical_experiment_ledger_json(event)
    return (
        event.event_id,
        event.capsule_id,
        event.session_date.isoformat(),
        event.attempted_bar_cursor.isoformat(),
        event.evaluation_id,
        hashlib.sha256(payload.encode()).hexdigest(),
        payload,
    )


def _event_from_row(row: _EventRow) -> KrDayCapsuleShadowEvent:
    event = KrDayCapsuleShadowEvent.model_validate_json(row[-1])
    if row != _row(event):
        raise InvalidKrDayCapsuleShadowStoreError
    return event


def _prepare(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() == (0,):
        connection.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={_SCHEMA_VERSION};COMMIT;")
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
        raise InvalidKrDayCapsuleShadowStoreError
    objects = frozenset(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )
    if objects != _OBJECTS:
        raise InvalidKrDayCapsuleShadowStoreError


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKrDayCapsuleShadowStoreError


__all__ = ("InvalidKrDayCapsuleShadowStoreError", "KrDayCapsuleShadowStore")
