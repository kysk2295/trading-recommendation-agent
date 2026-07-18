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
from trading_agent.data_foundation_manifest import DataFoundationManifest
from trading_agent.strategy_data_gate import StrategyDataStatus
from trading_agent.us_opportunity_scanner_bundle import load_latest_us_opportunity_scanner_bundle
from trading_agent.us_opportunity_scanner_models import (
    StoredUsOpportunityRaw,
    UsOpportunityScannerBundle,
    UsOpportunityScannerProjectionError,
    UsOpportunityScannerProjectionRecord,
    decode_broad_scanner_snapshot,
    encode_broad_scanner_snapshot,
)
from trading_agent.us_opportunity_scanner_schema import (
    CREATE_US_OPPORTUNITY_SCANNER_SCHEMA,
    MIGRATE_US_OPPORTUNITY_SCANNER_V1_TO_V2,
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
                    "SELECT dataset_directory FROM us_opportunity_scanner_projections WHERE projection_key = ?",
                    (projection_key,),
                ).fetchone()
            return None if row is None else Path(row[0])
        except sqlite3.Error:
            raise UsOpportunityScannerProjectionError from None

    def append_projection(
        self,
        record: UsOpportunityScannerProjectionRecord,
    ) -> None:
        try:
            row = (
                record.dataset_id,
                record.projection_key,
                record.opportunity_id,
                str(record.dataset_directory),
                encode_broad_scanner_snapshot(record.snapshot),
                record.foundation.manifest_id,
                record.foundation.model_dump_json().encode(),
                record.security_master_id,
                record.recorded_at.isoformat(),
            )
            with _writer(self.path) as connection:
                existing = connection.execute(
                    "SELECT dataset_id,projection_key,opportunity_id,dataset_directory,"
                    "snapshot_payload,foundation_manifest_id,foundation_payload,"
                    "security_master_id,recorded_at "
                    "FROM us_opportunity_scanner_projections WHERE projection_key = ?",
                    (record.projection_key,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise UsOpportunityScannerProjectionError
                    return
                _ = connection.execute(
                    "INSERT INTO us_opportunity_scanner_projections "
                    "(dataset_id,projection_key,opportunity_id,dataset_directory,snapshot_payload,"
                    "foundation_manifest_id,foundation_payload,security_master_id,recorded_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
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
                row: tuple[str, bytes, str, bytes] | None = connection.execute(
                    "SELECT dataset_directory,snapshot_payload,foundation_manifest_id,"
                    "foundation_payload "
                    "FROM us_opportunity_scanner_projections ORDER BY generation DESC LIMIT 1"
                ).fetchone()
            if row is None:
                return None
            replay = replay_canonical_dataset(Path(row[0]))
            snapshot = decode_broad_scanner_snapshot(row[1], replay)
            _ = _decode_foundation(row[3], row[2], snapshot.observed_at)
            return snapshot
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise UsOpportunityScannerProjectionError from None

    def latest_foundation(self) -> DataFoundationManifest | None:
        if not self.path.is_file():
            return None
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                _require_schema(connection)
                row: tuple[str, bytes, str] | None = connection.execute(
                    "SELECT foundation_manifest_id,foundation_payload,recorded_at "
                    "FROM us_opportunity_scanner_projections ORDER BY generation DESC LIMIT 1"
                ).fetchone()
            if row is None:
                return None
            return _decode_foundation(
                row[1],
                row[0],
                dt.datetime.fromisoformat(row[2]),
            )
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise UsOpportunityScannerProjectionError from None

    def latest_bundle(self) -> UsOpportunityScannerBundle | None:
        return load_latest_us_opportunity_scanner_bundle(self.path)

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
    elif version == (1,):
        connection.executescript(MIGRATE_US_OPPORTUNITY_SCANNER_V1_TO_V2)
        _ = connection.execute(f"PRAGMA user_version = {US_OPPORTUNITY_SCANNER_SCHEMA_VERSION}")
        connection.commit()
    _require_schema(connection)


def _decode_foundation(
    payload: bytes,
    manifest_id: str,
    observed_at: dt.datetime,
) -> DataFoundationManifest:
    foundation = DataFoundationManifest.model_validate_json(payload)
    if (
        foundation.manifest_id != manifest_id
        or foundation.evaluated_at > observed_at
        or foundation.evaluate_data_readiness().status is not StrategyDataStatus.READY
    ):
        raise UsOpportunityScannerProjectionError
    return foundation


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (US_OPPORTUNITY_SCANNER_SCHEMA_VERSION,):
        raise UsOpportunityScannerProjectionError


__all__ = ("UsOpportunityScannerStore",)
