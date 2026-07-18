from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import final

from pydantic import ValidationError

from trading_agent.alpaca_security_master_models import (
    AlpacaSecurityMasterError,
    AlpacaSecurityMasterSnapshot,
    StoredAlpacaSecurityMasterRaw,
)
from trading_agent.alpaca_security_master_schema import (
    ALPACA_SECURITY_MASTER_SCHEMA_VERSION,
    CREATE_ALPACA_SECURITY_MASTER_SCHEMA,
)


@final
class AlpacaSecurityMasterStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def raw_count(self) -> int:
        return self._count("alpaca_security_master_raw")

    def snapshot_count(self) -> int:
        return self._count("alpaca_security_master_snapshots")

    def append_raw(
        self,
        observed_at: dt.datetime,
        raw_payload: bytes,
    ) -> StoredAlpacaSecurityMasterRaw:
        try:
            payload_sha256 = hashlib.sha256(raw_payload).hexdigest()
            receipt_id = _receipt_id(observed_at, payload_sha256)
            with _writer(self.path) as connection:
                existing = connection.execute(
                    "SELECT generation,receipt_id,observed_at,payload_sha256,raw_payload "
                    "FROM alpaca_security_master_raw WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()
                if existing is not None:
                    stored = _stored_raw(existing)
                    if stored.raw_payload != raw_payload:
                        raise AlpacaSecurityMasterError
                    return stored
                cursor = connection.execute(
                    "INSERT INTO alpaca_security_master_raw "
                    "(receipt_id,observed_at,payload_sha256,raw_payload) VALUES (?,?,?,?)",
                    (receipt_id, observed_at.isoformat(), payload_sha256, raw_payload),
                )
                connection.commit()
                if type(cursor.lastrowid) is not int:
                    raise AlpacaSecurityMasterError
                return StoredAlpacaSecurityMasterRaw(
                    cursor.lastrowid,
                    receipt_id,
                    observed_at,
                    payload_sha256,
                    raw_payload,
                )
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSecurityMasterError from None

    def append_snapshot(self, snapshot: AlpacaSecurityMasterSnapshot) -> None:
        try:
            payload = snapshot.model_dump_json().encode()
            row = (
                snapshot.snapshot_id,
                snapshot.raw_receipt_id,
                snapshot.observed_at.isoformat(),
                payload,
            )
            with _writer(self.path) as connection:
                raw = connection.execute(
                    "SELECT observed_at FROM alpaca_security_master_raw WHERE receipt_id = ?",
                    (snapshot.raw_receipt_id,),
                ).fetchone()
                if raw != (snapshot.observed_at.isoformat(),):
                    raise AlpacaSecurityMasterError
                existing = connection.execute(
                    "SELECT snapshot_id,raw_receipt_id,observed_at,snapshot_payload "
                    "FROM alpaca_security_master_snapshots WHERE snapshot_id = ?",
                    (snapshot.snapshot_id,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise AlpacaSecurityMasterError
                    return
                _ = connection.execute(
                    "INSERT INTO alpaca_security_master_snapshots "
                    "(snapshot_id,raw_receipt_id,observed_at,snapshot_payload) VALUES (?,?,?,?)",
                    row,
                )
                connection.commit()
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise AlpacaSecurityMasterError from None

    def latest_snapshot(self) -> AlpacaSecurityMasterSnapshot | None:
        if not self.path.is_file():
            return None
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                _require_schema(connection)
                row: tuple[bytes, str, str, bytes] | None = connection.execute(
                    "SELECT s.snapshot_payload,r.observed_at,r.payload_sha256,r.raw_payload "
                    "FROM alpaca_security_master_snapshots s JOIN alpaca_security_master_raw r "
                    "ON r.receipt_id=s.raw_receipt_id ORDER BY s.generation DESC LIMIT 1"
                ).fetchone()
            if row is None:
                return None
            snapshot = AlpacaSecurityMasterSnapshot.model_validate_json(row[0])
            payload_sha256 = hashlib.sha256(row[3]).hexdigest()
            if (
                snapshot.observed_at.isoformat() != row[1]
                or payload_sha256 != row[2]
                or snapshot.raw_receipt_id != _receipt_id(snapshot.observed_at, payload_sha256)
            ):
                raise AlpacaSecurityMasterError
            return snapshot
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise AlpacaSecurityMasterError from None

    def raw_payload(self, receipt_id: str) -> bytes:
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                _require_schema(connection)
                row: tuple[bytes] | None = connection.execute(
                    "SELECT raw_payload FROM alpaca_security_master_raw WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()
            if row is None:
                raise AlpacaSecurityMasterError
            return row[0]
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSecurityMasterError from None

    def _count(self, table: str) -> int:
        if not self.path.is_file():
            return 0
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                _require_schema(connection)
                row: tuple[int] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
            return row[0]
        except sqlite3.Error:
            raise AlpacaSecurityMasterError from None


@contextmanager
def _writer(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(f"{path}.writer.lock", os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        connection = sqlite3.connect(path)
        os.chmod(path, 0o600)
        try:
            _prepare(connection)
            yield connection
        finally:
            connection.close()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _receipt_id(observed_at: dt.datetime, payload_sha256: str) -> str:
    encoded = json.dumps(
        {"observed_at": observed_at.isoformat(), "payload_sha256": payload_sha256},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _stored_raw(row: tuple[int, str, str, str, bytes]) -> StoredAlpacaSecurityMasterRaw:
    return StoredAlpacaSecurityMasterRaw(
        row[0],
        row[1],
        dt.datetime.fromisoformat(row[2]),
        row[3],
        row[4],
    )


def _prepare(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() == (0,):
        connection.executescript(CREATE_ALPACA_SECURITY_MASTER_SCHEMA)
        _ = connection.execute(f"PRAGMA user_version = {ALPACA_SECURITY_MASTER_SCHEMA_VERSION}")
        connection.commit()
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (
        ALPACA_SECURITY_MASTER_SCHEMA_VERSION,
    ):
        raise AlpacaSecurityMasterError


__all__ = ("AlpacaSecurityMasterStore",)
