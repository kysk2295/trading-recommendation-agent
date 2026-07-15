from __future__ import annotations

import datetime as dt
import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import final, override

from trading_agent.lane_contract_keys import (
    ExperimentScopeKey,
    LaneAccountBindingKey,
    LaneDailySnapshotKey,
    LaneManifestKey,
    canonical_lane_contract_json,
    experiment_scope_key,
    lane_account_binding_key,
    lane_daily_snapshot_key,
    lane_manifest_key,
)
from trading_agent.lane_contract_models import (
    ExperimentScope,
    LaneAccountBinding,
    LaneAccountBindingMode,
    LaneDailySnapshot,
    LaneManifest,
)
from trading_agent.lane_policy_models import (
    LaneId,
    LaneOrderAuthority,
    LaneRiskEnforcement,
)
from trading_agent.lane_registry_schema import (
    CREATE_LANE_REGISTRY_SCHEMA,
    LANE_REGISTRY_SCHEMA_VERSION,
)


class LaneRegistryConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "lane registry immutable identity의 내용이 다릅니다"


class InvalidLaneRegistrySourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "lane registry 항목의 manifest 또는 experiment scope 근거가 유효하지 않습니다"


class LaneRegistryWriterLeaseUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "lane registry single Writer lease를 획득하지 못했습니다"


class UnsupportedLaneRegistrySchemaError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "지원하지 않는 lane registry schema입니다"


class InactiveLaneRegistryWriterError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "종료된 lane registry Writer는 사용할 수 없습니다"


@dataclass(frozen=True, slots=True)
class StoredLaneManifest:
    manifest_key: LaneManifestKey
    manifest: LaneManifest


@dataclass(frozen=True, slots=True)
class StoredLaneAccountBinding:
    binding_key: LaneAccountBindingKey
    binding: LaneAccountBinding


@dataclass(frozen=True, slots=True)
class StoredExperimentScope:
    scope_key: ExperimentScopeKey
    scope: ExperimentScope


@dataclass(frozen=True, slots=True)
class StoredLaneDailySnapshot:
    snapshot_key: LaneDailySnapshotKey
    snapshot: LaneDailySnapshot


class LaneRegistryReader:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def is_initialized(self) -> bool:
        if not self.path.is_file():
            return False
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
            version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
        return version == (LANE_REGISTRY_SCHEMA_VERSION,)

    def manifests(self) -> tuple[StoredLaneManifest, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str]] = connection.execute(
                "SELECT manifest_key, payload_json FROM lane_manifests ORDER BY rowid"
            ).fetchall()
        return tuple(
            StoredLaneManifest(LaneManifestKey(key), LaneManifest.model_validate_json(payload)) for key, payload in rows
        )

    def account_bindings(self) -> tuple[StoredLaneAccountBinding, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str]] = connection.execute(
                "SELECT binding_key, payload_json FROM lane_account_bindings ORDER BY rowid"
            ).fetchall()
        return tuple(
            StoredLaneAccountBinding(
                LaneAccountBindingKey(key),
                LaneAccountBinding.model_validate_json(payload),
            )
            for key, payload in rows
        )

    def experiment_scopes(self) -> tuple[StoredExperimentScope, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str]] = connection.execute(
                "SELECT scope_key, payload_json FROM experiment_scopes ORDER BY rowid"
            ).fetchall()
        return tuple(
            StoredExperimentScope(ExperimentScopeKey(key), ExperimentScope.model_validate_json(payload))
            for key, payload in rows
        )

    def daily_snapshots(self) -> tuple[StoredLaneDailySnapshot, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str]] = connection.execute(
                "SELECT snapshot_key, payload_json FROM lane_daily_snapshots ORDER BY session_date, lane_id"
            ).fetchall()
        return tuple(
            StoredLaneDailySnapshot(
                LaneDailySnapshotKey(key),
                LaneDailySnapshot.model_validate_json(payload),
            )
            for key, payload in rows
        )

    def daily_snapshot(
        self,
        lane_id: LaneId,
        session_date: dt.date,
    ) -> StoredLaneDailySnapshot | None:
        if not self.path.is_file():
            return None
        with self._reader_connection() as connection:
            rows: list[tuple[str, str]] = connection.execute(
                """SELECT snapshot_key, payload_json FROM lane_daily_snapshots
                WHERE lane_id = ? AND session_date = ?""",
                (lane_id.value, session_date.isoformat()),
            ).fetchall()
        if len(rows) > 1:
            raise InvalidLaneRegistrySourceError
        if not rows:
            return None
        key, payload = rows[0]
        return StoredLaneDailySnapshot(
            LaneDailySnapshotKey(key),
            LaneDailySnapshot.model_validate_json(payload),
        )

    def _reader_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        _ = connection.execute("PRAGMA query_only = ON")
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _require_current_schema(connection)
        return connection


@final
class LaneRegistryStore(LaneRegistryReader):
    __slots__ = ()

    @contextmanager
    def writer(self) -> Iterator[LaneRegistryWriter]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise LaneRegistryWriterLeaseUnavailableError from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            os.chmod(self.path, 0o600)
            try:
                _prepare_writer_connection(connection)
                writer = LaneRegistryWriter(connection)
                try:
                    yield writer
                finally:
                    writer._close()
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@final
class LaneRegistryWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def register_manifest(self, manifest: LaneManifest) -> bool:
        self._require_active()
        key = lane_manifest_key(manifest)
        payload = canonical_lane_contract_json(manifest)
        return self._insert_immutable(
            table="lane_manifests",
            key_column="manifest_key",
            key=key,
            identity_where="lane_id = ? AND manifest_version = ?",
            identity_values=(manifest.lane_id.value, manifest.manifest_version),
            insert_sql="INSERT INTO lane_manifests VALUES (?, ?, ?, ?)",
            insert_values=(key, manifest.lane_id.value, manifest.manifest_version, payload),
            payload=payload,
        )

    def bind_account(self, binding: LaneAccountBinding) -> bool:
        self._require_active()
        manifests = tuple(
            LaneManifest.model_validate_json(row[0])
            for row in self._connection.execute(
                "SELECT payload_json FROM lane_manifests WHERE lane_id = ?",
                (binding.lane_id.value,),
            ).fetchall()
        )
        if not manifests or not all(_manifest_allows_account_binding(manifest) for manifest in manifests):
            raise InvalidLaneRegistrySourceError
        key = lane_account_binding_key(binding)
        payload = canonical_lane_contract_json(binding)
        existing: tuple[str, str] | None = self._connection.execute(
            "SELECT binding_key, payload_json FROM lane_account_bindings WHERE binding_key = ?",
            (key,),
        ).fetchone()
        if existing is not None:
            if existing == (key, payload):
                return False
            raise LaneRegistryConflictError
        identity = self._connection.execute(
            """SELECT 1 FROM lane_account_bindings
            WHERE lane_id = ? OR account_fingerprint = ? OR execution_ledger_fingerprint = ?""",
            (
                binding.lane_id.value,
                binding.account_fingerprint,
                binding.execution_ledger_fingerprint,
            ),
        ).fetchone()
        if identity is not None:
            raise LaneRegistryConflictError
        try:
            _ = self._connection.execute(
                "INSERT INTO lane_account_bindings VALUES (?, ?, ?, ?, ?)",
                (
                    key,
                    binding.lane_id.value,
                    binding.account_fingerprint,
                    binding.execution_ledger_fingerprint,
                    payload,
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise LaneRegistryConflictError from error
        return True

    def register_experiment_scope(self, scope: ExperimentScope) -> bool:
        self._require_active()
        key = experiment_scope_key(scope)
        payload = canonical_lane_contract_json(scope)
        return self._insert_immutable(
            table="experiment_scopes",
            key_column="scope_key",
            key=key,
            identity_where="hypothesis_id = ?",
            identity_values=(scope.hypothesis_id,),
            insert_sql="INSERT INTO experiment_scopes VALUES (?, ?, ?, ?)",
            insert_values=(key, scope.hypothesis_id, scope.primary_lane.value, payload),
            payload=payload,
        )

    def append_daily_snapshot(self, snapshot: LaneDailySnapshot) -> bool:
        self._require_active()
        manifest: tuple[str] | None = self._connection.execute(
            "SELECT payload_json FROM lane_manifests WHERE manifest_key = ?",
            (snapshot.manifest_key,),
        ).fetchone()
        if manifest is None or LaneManifest.model_validate_json(manifest[0]).lane_id is not snapshot.lane_id:
            raise InvalidLaneRegistrySourceError
        scopes = tuple(
            ExperimentScope.model_validate_json(row[0])
            for key in snapshot.experiment_scope_keys
            for row in self._connection.execute(
                "SELECT payload_json FROM experiment_scopes WHERE scope_key = ?",
                (key,),
            ).fetchall()
        )
        if len(scopes) != len(snapshot.experiment_scope_keys) or any(
            snapshot.lane_id not in scope.lanes for scope in scopes
        ):
            raise InvalidLaneRegistrySourceError
        key = lane_daily_snapshot_key(snapshot)
        payload = canonical_lane_contract_json(snapshot)
        return self._insert_immutable(
            table="lane_daily_snapshots",
            key_column="snapshot_key",
            key=key,
            identity_where="lane_id = ? AND session_date = ?",
            identity_values=(snapshot.lane_id.value, snapshot.session_date.isoformat()),
            insert_sql="INSERT INTO lane_daily_snapshots VALUES (?, ?, ?, ?, ?)",
            insert_values=(
                key,
                snapshot.lane_id.value,
                snapshot.session_date.isoformat(),
                snapshot.manifest_key,
                payload,
            ),
            payload=payload,
        )

    def _insert_immutable(
        self,
        *,
        table: str,
        key_column: str,
        key: str,
        identity_where: str,
        identity_values: tuple[object, ...],
        insert_sql: str,
        insert_values: tuple[object, ...],
        payload: str,
    ) -> bool:
        existing: tuple[str] | None = self._connection.execute(
            f"SELECT payload_json FROM {table} WHERE {key_column} = ?",
            (key,),
        ).fetchone()
        if existing is not None:
            if existing == (payload,):
                return False
            raise LaneRegistryConflictError
        identity = self._connection.execute(
            f"SELECT payload_json FROM {table} WHERE {identity_where}",
            identity_values,
        ).fetchone()
        if identity is not None:
            raise LaneRegistryConflictError
        try:
            _ = self._connection.execute(insert_sql, insert_values)
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise LaneRegistryConflictError from error
        return True

    def _require_active(self) -> None:
        if not self._active:
            raise InactiveLaneRegistryWriterError

    def _close(self) -> None:
        if self._active:
            self._active = False
            self._connection.close()


def _manifest_allows_account_binding(manifest: LaneManifest) -> bool:
    return (
        manifest.account_binding_mode is LaneAccountBindingMode.DEDICATED_PAPER
        and manifest.execution_policy.order_authority is LaneOrderAuthority.ALPACA_PAPER
        and manifest.risk_contract.enforcement is LaneRiskEnforcement.BROKER_PAPER
    )


def _prepare_writer_connection(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    _ = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    current = 0 if version is None else version[0]
    if current == 0:
        objects = tuple(
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        )
        if objects:
            raise UnsupportedLaneRegistrySchemaError
        connection.executescript(CREATE_LANE_REGISTRY_SCHEMA)
        _ = connection.execute(f"PRAGMA user_version = {LANE_REGISTRY_SCHEMA_VERSION}")
        connection.commit()
        return
    _require_current_schema(connection)


def _require_current_schema(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version != (LANE_REGISTRY_SCHEMA_VERSION,):
        raise UnsupportedLaneRegistrySchemaError
