from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import final

from trading_agent.us_market_data_fleet_audit import (
    RuntimeFleetAuditError,
    RuntimeFleetAuditRecord,
    record_bytes,
    record_from_bytes,
)

_SCHEMA = """
CREATE TABLE runtime_fleet_audit (
  generation INTEGER PRIMARY KEY AUTOINCREMENT,
  cycle_id TEXT NOT NULL UNIQUE,
  evaluated_at TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json BLOB NOT NULL
);
CREATE TRIGGER runtime_fleet_audit_no_update BEFORE UPDATE ON runtime_fleet_audit
BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER runtime_fleet_audit_no_delete BEFORE DELETE ON runtime_fleet_audit
BEGIN SELECT RAISE(ABORT, 'append only'); END;
"""


@final
class RuntimeFleetAuditStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def append(self, record: RuntimeFleetAuditRecord) -> bool:
        try:
            payload = record_bytes(record)
            row = (
                record.cycle_id,
                record.evaluated_at.isoformat(),
                hashlib.sha256(payload).hexdigest(),
                payload,
            )
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT cycle_id,evaluated_at,payload_sha256,payload_json "
                    "FROM runtime_fleet_audit WHERE cycle_id=?",
                    (record.cycle_id,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise RuntimeFleetAuditError
                    return False
                connection.execute(
                    "INSERT INTO runtime_fleet_audit "
                    "(cycle_id,evaluated_at,payload_sha256,payload_json) VALUES (?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise RuntimeFleetAuditError from None

    def latest(self) -> RuntimeFleetAuditRecord | None:
        if self.path.is_symlink():
            raise RuntimeFleetAuditError
        if not self.path.is_file():
            return None
        try:
            with closing(self._connection(write=False)) as connection:
                row: tuple[str, str, str, bytes] | None = connection.execute(
                    "SELECT cycle_id,evaluated_at,payload_sha256,payload_json "
                    "FROM runtime_fleet_audit ORDER BY generation DESC LIMIT 1"
                ).fetchone()
            if row is None or hashlib.sha256(row[3]).hexdigest() != row[2]:
                raise RuntimeFleetAuditError
            record = record_from_bytes(row[3])
            if record.cycle_id != row[0] or record.evaluated_at.isoformat() != row[1]:
                raise RuntimeFleetAuditError
            return record
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise RuntimeFleetAuditError from None

    def _connection(self, *, write: bool) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise RuntimeFleetAuditError
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
        if connection.execute("PRAGMA user_version").fetchone() != (1,):
            connection.close()
            raise RuntimeFleetAuditError
        return connection


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise RuntimeFleetAuditError


__all__ = ("RuntimeFleetAuditStore",)
