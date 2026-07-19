from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import final

from trading_agent.us_runtime_minute_supervisor import (
    RuntimeMinuteSupervisorError,
    RuntimeMinuteSupervisorRecord,
    record_bytes,
    record_from_bytes,
)

_SCHEMA = """
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
_OBJECTS = {
    "runtime_minute_supervisor",
    "runtime_minute_supervisor_no_delete",
    "runtime_minute_supervisor_no_update",
}


@final
class RuntimeMinuteSupervisorStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def append(self, record: RuntimeMinuteSupervisorRecord) -> bool:
        try:
            payload = record_bytes(record)
            row = (
                record.attempt_id,
                record.started_at.isoformat(),
                hashlib.sha256(payload).hexdigest(),
                payload,
            )
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT attempt_id,started_at,payload_sha256,payload_json "
                    "FROM runtime_minute_supervisor WHERE attempt_id=?",
                    (record.attempt_id,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise RuntimeMinuteSupervisorError
                    return False
                connection.execute(
                    "INSERT INTO runtime_minute_supervisor "
                    "(attempt_id,started_at,payload_sha256,payload_json) VALUES (?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
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
                os.chmod(self.path, 0o600)
            _require_private_file(self.path)
            if connection.execute("PRAGMA user_version").fetchone() == (0,):
                connection.executescript(_SCHEMA)
                connection.execute("PRAGMA user_version=1")
                connection.commit()
        else:
            _require_private_file(self.path)
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'"
            )
        }
        if connection.execute("PRAGMA user_version").fetchone() != (1,) or objects != _OBJECTS:
            connection.close()
            raise RuntimeMinuteSupervisorError
        return connection


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise RuntimeMinuteSupervisorError


__all__ = ("RuntimeMinuteSupervisorStore",)
