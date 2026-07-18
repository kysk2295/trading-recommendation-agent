from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import final

from pydantic import ValidationError

from trading_agent.data_capability_models import DataCapability, DataEntitlement, DataSourceId
from trading_agent.data_capability_registry_models import (
    DataCapabilityRegistryError,
    DataCapabilityRegistrySnapshot,
    RegistryAppendResult,
)
from trading_agent.data_capability_registry_schema import (
    CREATE_DATA_CAPABILITY_REGISTRY_SCHEMA,
    DATA_CAPABILITY_REGISTRY_SCHEMA_VERSION,
)
from trading_agent.data_capability_registry_support import (
    active_entitlement,
    aware,
    canonical_bytes,
    latest_capability,
    reject_overlapping_entitlement,
    require_private_file,
    utc_text,
    validated_inputs,
    validated_sources,
)


@final
class DataCapabilityRegistryStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def append(
        self,
        capabilities: Sequence[DataCapability],
        entitlements: Sequence[DataEntitlement],
    ) -> RegistryAppendResult:
        try:
            checked_capabilities, checked_entitlements = validated_inputs(capabilities, entitlements)
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                entitlement_count = sum(self._append_entitlement(connection, item) for item in checked_entitlements)
                capability_count = sum(self._append_capability(connection, item) for item in checked_capabilities)
                connection.commit()
            return RegistryAppendResult(capability_count, entitlement_count)
        except DataCapabilityRegistryError:
            raise
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise DataCapabilityRegistryError from None

    def snapshot(
        self,
        *,
        as_of: dt.datetime,
        source_ids: Sequence[DataSourceId],
    ) -> DataCapabilityRegistrySnapshot:
        try:
            checked_sources = validated_sources(source_ids)
            if not aware(as_of):
                raise ValueError
            capabilities: list[DataCapability] = []
            entitlements: list[DataEntitlement] = []
            missing_capabilities: list[str] = []
            missing_entitlements: list[str] = []
            with closing(self._connection(write=False)) as connection:
                for source in checked_sources:
                    capability = latest_capability(connection, source, as_of)
                    entitlement = active_entitlement(connection, source, as_of)
                    if capability is None:
                        missing_capabilities.append(source.canonical_id)
                    else:
                        capabilities.append(capability)
                    if entitlement is None:
                        missing_entitlements.append(source.canonical_id)
                    else:
                        entitlements.append(entitlement)
            return DataCapabilityRegistrySnapshot(
                as_of=as_of,
                capabilities=tuple(capabilities),
                entitlements=tuple(entitlements),
                missing_capability_source_ids=tuple(missing_capabilities),
                missing_entitlement_source_ids=tuple(missing_entitlements),
            )
        except DataCapabilityRegistryError:
            raise
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise DataCapabilityRegistryError from None

    def _append_entitlement(self, connection: sqlite3.Connection, item: DataEntitlement) -> int:
        payload = canonical_bytes(item)
        row = (
            item.entitlement_id,
            item.source_id.canonical_id,
            utc_text(item.effective_from),
            utc_text(item.effective_to) if item.effective_to is not None else None,
            hashlib.sha256(payload).hexdigest(),
            payload,
        )
        existing = connection.execute(
            "SELECT entitlement_id,source_id,effective_from_utc,effective_to_utc,payload_sha256,payload_json "
            "FROM entitlements WHERE entitlement_id=?",
            (item.entitlement_id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != row:
                raise DataCapabilityRegistryError
            return 0
        reject_overlapping_entitlement(connection, item)
        connection.execute("INSERT INTO entitlements VALUES (NULL,?,?,?,?,?,?)", row)
        return 1

    def _append_capability(self, connection: sqlite3.Connection, item: DataCapability) -> int:
        payload = canonical_bytes(item)
        payload_sha = hashlib.sha256(payload).hexdigest()
        row = (
            payload_sha,
            item.source_id.canonical_id,
            utc_text(item.assessed_at),
            payload_sha,
            payload,
        )
        existing = connection.execute(
            "SELECT assessment_id,source_id,assessed_at_utc,payload_sha256,payload_json "
            "FROM capability_assessments WHERE source_id=? AND assessed_at_utc=?",
            (item.source_id.canonical_id, utc_text(item.assessed_at)),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != row:
                raise DataCapabilityRegistryError
            return 0
        connection.execute("INSERT INTO capability_assessments VALUES (NULL,?,?,?,?,?)", row)
        return 1

    def _connection(self, *, write: bool) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise DataCapabilityRegistryError
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existed = self.path.exists()
            if existed:
                require_private_file(self.path)
            connection = sqlite3.connect(self.path)
            if not existed:
                os.chmod(self.path, 0o600)
            require_private_file(self.path)
            if connection.execute("PRAGMA user_version").fetchone() == (0,):
                connection.executescript(CREATE_DATA_CAPABILITY_REGISTRY_SCHEMA)
                connection.execute(f"PRAGMA user_version={DATA_CAPABILITY_REGISTRY_SCHEMA_VERSION}")
                connection.commit()
        else:
            require_private_file(self.path)
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA user_version").fetchone() != (DATA_CAPABILITY_REGISTRY_SCHEMA_VERSION,):
            connection.close()
            raise DataCapabilityRegistryError
        return connection


__all__ = (
    "DataCapabilityRegistryError",
    "DataCapabilityRegistrySnapshot",
    "DataCapabilityRegistryStore",
    "RegistryAppendResult",
)
