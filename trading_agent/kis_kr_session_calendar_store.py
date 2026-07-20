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
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_models import (
    KisKrSessionCalendarReceipt,
    KrSessionCalendarSnapshot,
)
from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.sqlite_uri import sqlite_read_only_uri

_SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE kis_kr_session_calendars (
  snapshot_key TEXT PRIMARY KEY,
  base_date TEXT NOT NULL UNIQUE,
  observed_at TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL,
  raw_payload BLOB NOT NULL,
  snapshot_sha256 TEXT NOT NULL,
  snapshot_json TEXT NOT NULL
);
CREATE INDEX kis_kr_session_calendars_by_date
ON kis_kr_session_calendars(base_date, observed_at);
CREATE TRIGGER kis_kr_session_calendars_no_update
BEFORE UPDATE ON kis_kr_session_calendars BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kis_kr_session_calendars_no_delete
BEFORE DELETE ON kis_kr_session_calendars BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_OBJECTS: Final = frozenset(
    {
        "kis_kr_session_calendars",
        "kis_kr_session_calendars_by_date",
        "kis_kr_session_calendars_no_update",
        "kis_kr_session_calendars_no_delete",
    }
)


class InvalidKisKrSessionCalendarStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR session calendar store is invalid"


@final
class KisKrSessionCalendarStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def snapshots(self) -> tuple[KrSessionCalendarSnapshot, ...]:
        if self.path.is_symlink():
            raise InvalidKisKrSessionCalendarStoreError
        if not self.path.exists():
            return ()
        try:
            _require_private_file(self.path)
            with closing(sqlite3.connect(sqlite_read_only_uri(self.path), uri=True)) as connection:
                _ = connection.execute("PRAGMA query_only = ON")
                _require_schema(connection)
                rows: list[tuple[str, str, str, str, bytes, str, str]] = connection.execute(
                    "SELECT snapshot_key,base_date,observed_at,raw_sha256,raw_payload,"
                    "snapshot_sha256,snapshot_json FROM kis_kr_session_calendars ORDER BY base_date"
                ).fetchall()
            return tuple(_snapshot_from_row(row) for row in rows)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKisKrSessionCalendarStoreError from None

    def append(
        self,
        receipt: KisKrSessionCalendarReceipt,
        snapshot: KrSessionCalendarSnapshot,
    ) -> bool:
        try:
            snapshot = KrSessionCalendarSnapshot.model_validate(snapshot.model_dump(mode="python"))
            if project_kis_kr_session_calendar(receipt) != snapshot:
                raise InvalidKisKrSessionCalendarStoreError
            _ = self.snapshots()
            if self.path.is_symlink():
                raise InvalidKisKrSessionCalendarStoreError
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            with closing(sqlite3.connect(self.path, timeout=0.0)) as connection:
                _prepare(connection)
                os.chmod(self.path, 0o600)
                connection.execute("BEGIN IMMEDIATE")
                row = _row(receipt, snapshot)
                existing: tuple[str, str, str, str, bytes, str, str] | None = connection.execute(
                    "SELECT snapshot_key,base_date,observed_at,raw_sha256,raw_payload,"
                    "snapshot_sha256,snapshot_json FROM kis_kr_session_calendars WHERE base_date=?",
                    (receipt.base_date.isoformat(),),
                ).fetchone()
                if existing is not None:
                    if existing != row:
                        raise InvalidKisKrSessionCalendarStoreError
                    connection.rollback()
                    return False
                _ = connection.execute("INSERT INTO kis_kr_session_calendars VALUES (?,?,?,?,?,?,?)", row)
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKisKrSessionCalendarStoreError from None


def _row(
    receipt: KisKrSessionCalendarReceipt,
    snapshot: KrSessionCalendarSnapshot,
) -> tuple[str, str, str, str, bytes, str, str]:
    payload = canonical_experiment_ledger_json(snapshot)
    return (
        snapshot.snapshot_id,
        receipt.base_date.isoformat(),
        receipt.received_at.isoformat(),
        receipt.payload_sha256,
        receipt.raw_payload,
        hashlib.sha256(payload.encode()).hexdigest(),
        payload,
    )


def _snapshot_from_row(row: tuple[str, str, str, str, bytes, str, str]) -> KrSessionCalendarSnapshot:
    key, base_date, observed_at, raw_sha, raw_payload, snapshot_sha, payload = row
    snapshot = KrSessionCalendarSnapshot.model_validate_json(payload)
    receipt = KisKrSessionCalendarReceipt(
        base_date=dt.date.fromisoformat(base_date),
        received_at=dt.datetime.fromisoformat(observed_at),
        status_code=200,
        content_type="application/json",
        raw_payload=raw_payload,
    )
    if (
        row != _row(receipt, snapshot)
        or key != snapshot.snapshot_id
        or raw_sha != hashlib.sha256(raw_payload).hexdigest()
        or snapshot_sha != hashlib.sha256(payload.encode()).hexdigest()
        or project_kis_kr_session_calendar(receipt) != snapshot
    ):
        raise InvalidKisKrSessionCalendarStoreError
    return snapshot


def _prepare(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={_SCHEMA_VERSION};COMMIT;")
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
        raise InvalidKisKrSessionCalendarStoreError
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    )
    if objects != _OBJECTS:
        raise InvalidKisKrSessionCalendarStoreError


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKisKrSessionCalendarStoreError
