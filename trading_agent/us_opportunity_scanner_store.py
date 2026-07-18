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

from trading_agent.canonical_duckdb_replay import replay_canonical_dataset
from trading_agent.us_opportunity_scanner_models import (
    StoredUsOpportunityRaw,
    UsOpportunityScannerProjectionError,
    decode_broad_scanner_snapshot,
    encode_broad_scanner_snapshot,
)
from trading_agent.us_opportunity_scanner_schema import (
    CREATE_US_OPPORTUNITY_SCANNER_SCHEMA,
    US_OPPORTUNITY_SCANNER_SCHEMA_VERSION,
)
from trading_agent.us_subscription_models import BroadScannerSnapshot


@final
class UsOpportunityScannerStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def raw_count(self) -> int:
        return self._count("us_opportunity_scanner_raw")

    def projection_count(self) -> int:
        return self._count("us_opportunity_scanner_projections")

    def append_raw(
        self,
        opportunity_id: str,
        observed_at: dt.datetime,
        raw_payload: bytes,
    ) -> StoredUsOpportunityRaw:
        try:
            payload_sha256 = hashlib.sha256(raw_payload).hexdigest()
            receipt_id = _receipt_id(opportunity_id, observed_at, payload_sha256)
            with _writer(self.path) as connection:
                existing = connection.execute(
                    "SELECT generation,receipt_id,opportunity_id,observed_at,payload_sha256,raw_payload "
                    "FROM us_opportunity_scanner_raw WHERE opportunity_id = ?",
                    (opportunity_id,),
                ).fetchone()
                if existing is not None:
                    stored = _stored_raw(existing)
                    if stored.receipt_id != receipt_id or stored.raw_payload != raw_payload:
                        raise UsOpportunityScannerProjectionError
                    return stored
                cursor = connection.execute(
                    "INSERT INTO us_opportunity_scanner_raw "
                    "(receipt_id,opportunity_id,observed_at,payload_sha256,raw_payload) VALUES (?,?,?,?,?)",
                    (receipt_id, opportunity_id, observed_at.isoformat(), payload_sha256, raw_payload),
                )
                connection.commit()
                generation = cursor.lastrowid
                if type(generation) is not int:
                    raise UsOpportunityScannerProjectionError
                return StoredUsOpportunityRaw(
                    generation,
                    receipt_id,
                    opportunity_id,
                    observed_at,
                    payload_sha256,
                    raw_payload,
                )
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise UsOpportunityScannerProjectionError from None

    def projection_directory(self, projection_key: str) -> Path | None:
        if not self.path.is_file():
            return None
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                _require_schema(connection)
                row: tuple[str] | None = connection.execute(
                    "SELECT dataset_directory FROM us_opportunity_scanner_projections "
                    "WHERE projection_key = ?",
                    (projection_key,),
                ).fetchone()
            return None if row is None else Path(row[0])
        except sqlite3.Error:
            raise UsOpportunityScannerProjectionError from None

    def append_projection(
        self,
        dataset_id: str,
        projection_key: str,
        opportunity_id: str,
        dataset_directory: Path,
        snapshot: BroadScannerSnapshot,
        recorded_at: dt.datetime,
    ) -> None:
        try:
            row = (
                dataset_id,
                projection_key,
                opportunity_id,
                str(dataset_directory),
                encode_broad_scanner_snapshot(snapshot),
                recorded_at.isoformat(),
            )
            with _writer(self.path) as connection:
                existing = connection.execute(
                    "SELECT dataset_id,projection_key,opportunity_id,dataset_directory,"
                    "snapshot_payload,recorded_at "
                    "FROM us_opportunity_scanner_projections WHERE projection_key = ?",
                    (projection_key,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise UsOpportunityScannerProjectionError
                    return
                _ = connection.execute(
                    "INSERT INTO us_opportunity_scanner_projections "
                    "(dataset_id,projection_key,opportunity_id,dataset_directory,snapshot_payload,"
                    "recorded_at) VALUES (?,?,?,?,?,?)",
                    row,
                )
                connection.commit()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise UsOpportunityScannerProjectionError from None

    def latest_snapshot(self) -> BroadScannerSnapshot | None:
        if not self.path.is_file():
            return None
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                _require_schema(connection)
                row: tuple[str, bytes] | None = connection.execute(
                    "SELECT dataset_directory,snapshot_payload "
                    "FROM us_opportunity_scanner_projections ORDER BY generation DESC LIMIT 1"
                ).fetchone()
            if row is None:
                return None
            replay = replay_canonical_dataset(Path(row[0]))
            return decode_broad_scanner_snapshot(row[1], replay)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise UsOpportunityScannerProjectionError from None

    def _count(self, table: str) -> int:
        if not self.path.is_file():
            return 0
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                _require_schema(connection)
                row: tuple[int] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
            return row[0]
        except sqlite3.Error:
            raise UsOpportunityScannerProjectionError from None


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


def _receipt_id(opportunity_id: str, observed_at: dt.datetime, payload_sha256: str) -> str:
    identity = {
        "observed_at": observed_at.isoformat(),
        "opportunity_id": opportunity_id,
        "payload_sha256": payload_sha256,
    }
    encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _stored_raw(row: tuple[int, str, str, str, str, bytes]) -> StoredUsOpportunityRaw:
    return StoredUsOpportunityRaw(
        row[0],
        row[1],
        row[2],
        dt.datetime.fromisoformat(row[3]),
        row[4],
        row[5],
    )


def _prepare(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(CREATE_US_OPPORTUNITY_SCANNER_SCHEMA)
        _ = connection.execute(f"PRAGMA user_version = {US_OPPORTUNITY_SCANNER_SCHEMA_VERSION}")
        connection.commit()
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (US_OPPORTUNITY_SCANNER_SCHEMA_VERSION,):
        raise UsOpportunityScannerProjectionError


__all__ = ("UsOpportunityScannerStore",)
