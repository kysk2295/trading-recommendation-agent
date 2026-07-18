from __future__ import annotations

import hashlib
import os
import sqlite3
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
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path)
            os.chmod(self.path, 0o600)
            if connection.execute("PRAGMA user_version").fetchone() == (0,):
                connection.executescript(_SCHEMA)
                connection.execute("PRAGMA user_version=1")
                connection.commit()
        else:
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA user_version").fetchone() != (1,):
            connection.close()
            raise RuntimeFleetAuditError
        return connection


__all__ = ("RuntimeFleetAuditStore",)
