from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Sequence
from pathlib import Path

from trading_agent.data_capability_models import DataCapability, DataEntitlement, DataSourceId
from trading_agent.data_capability_registry_models import DataCapabilityRegistryError


def validated_inputs(
    capabilities: Sequence[DataCapability],
    entitlements: Sequence[DataEntitlement],
) -> tuple[tuple[DataCapability, ...], tuple[DataEntitlement, ...]]:
    checked_capabilities = tuple(DataCapability.model_validate(item.model_dump(mode="python")) for item in capabilities)
    checked_entitlements = tuple(
        DataEntitlement.model_validate(item.model_dump(mode="python")) for item in entitlements
    )
    capability_ids = tuple(item.source_id.canonical_id for item in checked_capabilities)
    entitlement_ids = tuple(item.source_id.canonical_id for item in checked_entitlements)
    if (
        not checked_capabilities
        or capability_ids != tuple(sorted(set(capability_ids)))
        or entitlement_ids != capability_ids
    ):
        raise DataCapabilityRegistryError
    return checked_capabilities, checked_entitlements


def validated_sources(source_ids: Sequence[DataSourceId]) -> tuple[DataSourceId, ...]:
    checked = tuple(DataSourceId.model_validate(item.model_dump(mode="python")) for item in source_ids)
    canonical_ids = tuple(item.canonical_id for item in checked)
    if not checked or canonical_ids != tuple(sorted(set(canonical_ids))):
        raise DataCapabilityRegistryError
    return checked


def latest_capability(
    connection: sqlite3.Connection,
    source: DataSourceId,
    as_of: dt.datetime,
) -> DataCapability | None:
    row = connection.execute(
        "SELECT assessment_id,source_id,assessed_at_utc,payload_sha256,payload_json "
        "FROM capability_assessments "
        "WHERE source_id=? AND assessed_at_utc<=? ORDER BY assessed_at_utc DESC LIMIT 1",
        (source.canonical_id, utc_text(as_of)),
    ).fetchone()
    if row is None:
        return None
    item = DataCapability.model_validate_json(row[4])
    if (
        row[0] != row[3]
        or row[1] != source.canonical_id
        or item.source_id != source
        or utc_text(item.assessed_at) != row[2]
        or not valid_payload(item, row[3], row[4])
    ):
        raise DataCapabilityRegistryError
    return item


def active_entitlement(
    connection: sqlite3.Connection,
    source: DataSourceId,
    as_of: dt.datetime,
) -> DataEntitlement | None:
    rows = connection.execute(
        "SELECT entitlement_id,source_id,effective_from_utc,effective_to_utc,payload_sha256,payload_json "
        "FROM entitlements WHERE source_id=?",
        (source.canonical_id,),
    ).fetchall()
    active: list[DataEntitlement] = []
    for row in rows:
        item = DataEntitlement.model_validate_json(row[5])
        expected_to = utc_text(item.effective_to) if item.effective_to is not None else None
        if (
            row[0] != item.entitlement_id
            or row[1] != source.canonical_id
            or item.source_id != source
            or row[2] != utc_text(item.effective_from)
            or row[3] != expected_to
            or not valid_payload(item, row[4], row[5])
        ):
            raise DataCapabilityRegistryError
        if item.effective_from <= as_of and (item.effective_to is None or as_of < item.effective_to):
            active.append(item)
    if len(active) > 1:
        raise DataCapabilityRegistryError
    return active[0] if active else None


def reject_overlapping_entitlement(connection: sqlite3.Connection, item: DataEntitlement) -> None:
    rows = connection.execute(
        "SELECT payload_sha256,payload_json FROM entitlements WHERE source_id=?",
        (item.source_id.canonical_id,),
    ).fetchall()
    for row in rows:
        existing = DataEntitlement.model_validate_json(row[1])
        if not valid_payload(existing, row[0], row[1]):
            raise DataCapabilityRegistryError
        if intervals_overlap(existing, item):
            raise DataCapabilityRegistryError


def intervals_overlap(first: DataEntitlement, second: DataEntitlement) -> bool:
    return (first.effective_to is None or second.effective_from < first.effective_to) and (
        second.effective_to is None or first.effective_from < second.effective_to
    )


def valid_payload(item: DataCapability | DataEntitlement, digest: str, payload: bytes) -> bool:
    return hashlib.sha256(payload).hexdigest() == digest and canonical_bytes(item) == payload


def canonical_bytes(item: DataCapability | DataEntitlement) -> bytes:
    return json.dumps(item.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def utc_text(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise DataCapabilityRegistryError
