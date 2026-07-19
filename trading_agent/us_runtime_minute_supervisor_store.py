from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import final

from trading_agent.us_runtime_minute_supervisor import (
    RuntimeMinuteSupervisorError,
    RuntimeMinuteSupervisorRecord,
    record_bytes,
    record_from_bytes,
)
from trading_agent.us_runtime_supervisor_live_audit import (
    RuntimeSupervisorLiveAudit,
    live_audit_bytes,
    live_audit_from_bytes,
    validate_runtime_supervisor_live_audit,
)
from trading_agent.us_runtime_supervisor_store_security import runtime_supervisor_store_is_private

_SCHEMA_V1 = """
CREATE TABLE runtime_minute_supervisor (
 generation INTEGER PRIMARY KEY AUTOINCREMENT,
 attempt_id TEXT NOT NULL UNIQUE,
 started_at TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL,
 payload_json BLOB NOT NULL
);
CREATE TRIGGER runtime_minute_supervisor_no_update BEFORE UPDATE ON runtime_minute_supervisor
BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER runtime_minute_supervisor_no_delete BEFORE DELETE ON runtime_minute_supervisor
BEGIN SELECT RAISE(ABORT, 'append only'); END;
"""
_SCHEMA_V2 = """
CREATE TABLE runtime_live_actionability (
 generation INTEGER PRIMARY KEY AUTOINCREMENT,
 live_audit_id TEXT NOT NULL UNIQUE,
 attempt_id TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL,
 selected_count INTEGER NOT NULL,
 created_count INTEGER NOT NULL,
 replay_count INTEGER NOT NULL,
 payload_sha256 TEXT NOT NULL,
 payload_json BLOB NOT NULL
);
CREATE TRIGGER runtime_live_actionability_no_update BEFORE UPDATE ON runtime_live_actionability
BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER runtime_live_actionability_no_delete BEFORE DELETE ON runtime_live_actionability
BEGIN SELECT RAISE(ABORT, 'append only'); END;
"""
_OBJECTS_V1 = {
    "runtime_minute_supervisor",
    "runtime_minute_supervisor_no_delete",
    "runtime_minute_supervisor_no_update",
}
_OBJECTS_V2 = _OBJECTS_V1 | {
    "runtime_live_actionability",
    "runtime_live_actionability_no_delete",
    "runtime_live_actionability_no_update",
}


@final
class RuntimeMinuteSupervisorStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def append(self, record: RuntimeMinuteSupervisorRecord) -> bool:
        try:
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                appended = _insert_primary(connection, record)
                connection.commit()
            return appended
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise RuntimeMinuteSupervisorError from None

    def append_attempt(
        self,
        record: RuntimeMinuteSupervisorRecord,
        live_audit: RuntimeSupervisorLiveAudit,
    ) -> bool:
        try:
            validate_runtime_supervisor_live_audit(live_audit)
            if live_audit.attempt_id != record.attempt_id:
                raise RuntimeMinuteSupervisorError
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                primary_appended = _insert_primary(connection, record)
                live_appended = _insert_live(connection, live_audit)
                if primary_appended != live_appended:
                    raise RuntimeMinuteSupervisorError
                connection.commit()
            return primary_appended
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise RuntimeMinuteSupervisorError from None

    def records(self) -> tuple[RuntimeMinuteSupervisorRecord, ...]:
        if not self.path.is_file():
            return ()
        try:
            with closing(self._connection(write=False)) as connection:
                rows: list[tuple[str, str, str, bytes]] = connection.execute(
                    "SELECT attempt_id,started_at,payload_sha256,payload_json "
                    "FROM runtime_minute_supervisor ORDER BY generation"
                ).fetchall()
            records: list[RuntimeMinuteSupervisorRecord] = []
            for row in rows:
                if hashlib.sha256(row[3]).hexdigest() != row[2]:
                    raise RuntimeMinuteSupervisorError
                record = record_from_bytes(row[3])
                if record.attempt_id != row[0] or record.started_at.isoformat() != row[1]:
                    raise RuntimeMinuteSupervisorError
                records.append(record)
            return tuple(records)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise RuntimeMinuteSupervisorError from None

    def live_records(self) -> tuple[RuntimeSupervisorLiveAudit, ...]:
        if not self.path.is_file():
            return ()
        try:
            parents = self.records()
            parent_ids = {item.attempt_id for item in parents}
            with closing(self._connection(write=False)) as connection:
                if connection.execute("PRAGMA user_version").fetchone() == (1,):
                    return ()
                rows: list[tuple[str, str, str, int, int, int, str, bytes]] = connection.execute(
                    "SELECT live_audit_id,attempt_id,status,selected_count,created_count,replay_count,"
                    "payload_sha256,payload_json FROM runtime_live_actionability ORDER BY generation"
                ).fetchall()
            audits: list[RuntimeSupervisorLiveAudit] = []
            for row in rows:
                if row[1] not in parent_ids or hashlib.sha256(row[7]).hexdigest() != row[6]:
                    raise RuntimeMinuteSupervisorError
                audit = live_audit_from_bytes(row[7])
                if (
                    audit.live_audit_id,
                    audit.attempt_id,
                    audit.status.value,
                    audit.selected_count,
                    audit.created_count,
                    audit.replay_count,
                ) != row[:6]:
                    raise RuntimeMinuteSupervisorError
                audits.append(audit)
            result = tuple(audits)
            child_ids = {item.attempt_id for item in result}
            expected_order = tuple(item.attempt_id for item in parents if item.attempt_id in child_ids)
            if tuple(item.attempt_id for item in result) != expected_order:
                raise RuntimeMinuteSupervisorError
            return result
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise RuntimeMinuteSupervisorError from None

    def _connection(self, *, write: bool) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise RuntimeMinuteSupervisorError
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existed = self.path.exists()
            if existed:
                _require_private_file(self.path)
            connection = sqlite3.connect(self.path)
            if not existed:
                self.path.chmod(0o600)
            _require_private_file(self.path)
            if connection.execute("PRAGMA user_version").fetchone() == (0,):
                connection.executescript(_SCHEMA_V1)
                connection.execute("PRAGMA user_version=1")
                connection.commit()
            if connection.execute("PRAGMA user_version").fetchone() == (1,):
                _require_schema(connection, 1)
                connection.executescript(_SCHEMA_V2)
                connection.execute("PRAGMA user_version=2")
                connection.commit()
        else:
            _require_private_file(self.path)
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
        _require_schema(connection, None)
        return connection


def _insert_primary(connection: sqlite3.Connection, record: RuntimeMinuteSupervisorRecord) -> bool:
    payload = record_bytes(record)
    row = (
        record.attempt_id,
        record.started_at.isoformat(),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    existing = connection.execute(
        "SELECT attempt_id,started_at,payload_sha256,payload_json FROM runtime_minute_supervisor WHERE attempt_id=?",
        (record.attempt_id,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != row:
            raise RuntimeMinuteSupervisorError
        return False
    connection.execute(
        "INSERT INTO runtime_minute_supervisor (attempt_id,started_at,payload_sha256,payload_json) VALUES (?,?,?,?)",
        row,
    )
    return True


def _insert_live(connection: sqlite3.Connection, audit: RuntimeSupervisorLiveAudit) -> bool:
    payload = live_audit_bytes(audit)
    row = (
        audit.live_audit_id,
        audit.attempt_id,
        audit.status.value,
        audit.selected_count,
        audit.created_count,
        audit.replay_count,
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    existing = connection.execute(
        "SELECT live_audit_id,attempt_id,status,selected_count,created_count,replay_count,"
        "payload_sha256,payload_json FROM runtime_live_actionability WHERE live_audit_id=?",
        (audit.live_audit_id,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != row:
            raise RuntimeMinuteSupervisorError
        return False
    connection.execute(
        "INSERT INTO runtime_live_actionability "
        "(live_audit_id,attempt_id,status,selected_count,created_count,replay_count,"
        "payload_sha256,payload_json) VALUES (?,?,?,?,?,?,?,?)",
        row,
    )
    return True


def _require_schema(connection: sqlite3.Connection, expected: int | None) -> None:
    objects = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'"
        )
    }
    version = connection.execute("PRAGMA user_version").fetchone()
    if (
        (expected is not None and version != (expected,))
        or (version == (1,) and objects != _OBJECTS_V1)
        or (version == (2,) and objects != _OBJECTS_V2)
        or version not in {(1,), (2,)}
    ):
        raise RuntimeMinuteSupervisorError


def _require_private_file(path: Path) -> None:
    if not runtime_supervisor_store_is_private(path):
        raise RuntimeMinuteSupervisorError


__all__ = ("RuntimeMinuteSupervisorStore",)
