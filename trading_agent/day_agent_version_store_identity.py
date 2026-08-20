from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading_agent.day_agent_version_models import DayAgentVersionStoreError
from trading_agent.private_directory_identity import open_private_parent, require_open_directory_path
from trading_agent.systematic_regime_store_file import open_private_file, require_private_file

_IDENTITY_SUFFIX: Final = "day-agent-version-store.json"
_IDENTITY_LIMIT: Final = 4_096


class IdentityPhase(StrEnum):
    PREPARED = "prepared"
    COMMITTED = "committed"


class VersionStoreIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    database: tuple[int, int]
    lock: tuple[int, int]
    parent: tuple[int, int]
    path: str
    token: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: Literal[1, 2]
    phase: IdentityPhase = IdentityPhase.COMMITTED


@dataclass(frozen=True, slots=True)
class OpenVersionStore:
    parent: int
    database: int
    lock: int


@dataclass(frozen=True, slots=True)
class IdentityLocation:
    parent: int
    name: str


@dataclass(frozen=True, slots=True)
class IdentityContext:
    path: Path
    opened: OpenVersionStore
    anchor: IdentityLocation
    marker: IdentityLocation


def require_version_store_identity(path: Path, opened: OpenVersionStore, *, initialize: bool) -> None:
    require_open_directory_path(path.parent, opened.parent)
    _require_named_identity(opened.parent, path.name, opened.database)
    _require_named_identity(opened.parent, f"{path.name}.writer.lock", opened.lock)
    anchor_parent = open_private_parent(path.parent.parent, create=False)
    try:
        anchor = IdentityLocation(anchor_parent, f".{path.parent.name}.{path.name}.{_IDENTITY_SUFFIX}")
        marker = IdentityLocation(opened.parent, f".{path.name}.{_IDENTITY_SUFFIX}")
        context = IdentityContext(path, opened, anchor, marker)
        _require_identity_pair(context, initialize=initialize)
    except (OSError, TypeError, ValidationError, ValueError):
        raise DayAgentVersionStoreError("version_store_metadata_invalid") from None
    finally:
        os.close(anchor_parent)


def _require_identity_pair(context: IdentityContext, *, initialize: bool) -> None:
    locations = (
        context.anchor,
        context.marker,
        _temporary_location(context.anchor, IdentityPhase.PREPARED),
        _temporary_location(context.anchor, IdentityPhase.COMMITTED),
        _temporary_location(context.marker, IdentityPhase.PREPARED),
        _temporary_location(context.marker, IdentityPhase.COMMITTED),
    )
    records = tuple(_read_identity(location) for location in locations)
    present = tuple(record for record in records if record is not None)
    if not present:
        if not initialize:
            raise DayAgentVersionStoreError("version_store_metadata_invalid")
        candidate = _new_identity(context.path, context.opened)
    else:
        candidate = present[0]
        if any(not _same_binding(candidate, record) for record in present[1:]):
            raise DayAgentVersionStoreError("version_store_metadata_invalid")
    if not _matches_open_store(candidate, context.path, context.opened):
        raise DayAgentVersionStoreError("version_store_metadata_invalid")
    anchor_record, marker_record = records[:2]
    if (
        anchor_record is not None
        and marker_record is not None
        and anchor_record.phase is IdentityPhase.COMMITTED
        and marker_record.phase is IdentityPhase.COMMITTED
        and all(record is None for record in records[2:])
    ):
        return
    if not initialize or not _is_pristine_bootstrap(context.path, context.opened, context.marker):
        raise DayAgentVersionStoreError("version_store_metadata_invalid")
    prepared = candidate.model_copy(update={"phase": IdentityPhase.PREPARED, "version": 2})
    _complete_bootstrap(context.anchor, context.marker, prepared)


def _new_identity(path: Path, opened: OpenVersionStore) -> VersionStoreIdentity:
    parent = os.fstat(opened.parent)
    database = os.fstat(opened.database)
    lock = os.fstat(opened.lock)
    return VersionStoreIdentity(
        database=(database.st_dev, database.st_ino),
        lock=(lock.st_dev, lock.st_ino),
        parent=(parent.st_dev, parent.st_ino),
        path=str(path),
        token=secrets.token_hex(32),
        version=2,
        phase=IdentityPhase.PREPARED,
    )


def _complete_bootstrap(
    anchor: IdentityLocation,
    marker: IdentityLocation,
    prepared: VersionStoreIdentity,
) -> None:
    committed = prepared.model_copy(update={"phase": IdentityPhase.COMMITTED})
    for location, record in (
        (anchor, prepared),
        (marker, prepared),
        (marker, committed),
        (anchor, committed),
    ):
        _publish_identity(location, record)


def _publish_identity(location: IdentityLocation, record: VersionStoreIdentity) -> None:
    temporary = _temporary_location(location, record.phase)
    staged = _read_identity(temporary)
    if staged is None:
        descriptor = open_private_file(temporary.parent, temporary.name, create=True, write=True)
        try:
            encoded = record.model_dump_json().encode()
            _write_identity_file(descriptor, encoded)
            _sync_identity_file(descriptor)
        finally:
            os.close(descriptor)
    elif staged != record:
        raise DayAgentVersionStoreError("version_store_metadata_invalid")
    current = _read_identity(location)
    if current is not None and not _same_binding(current, record):
        raise DayAgentVersionStoreError("version_store_metadata_invalid")
    _replace_identity_file(temporary, location)
    _sync_identity_directory(location.parent)


def _read_identity(location: IdentityLocation) -> VersionStoreIdentity | None:
    try:
        descriptor = open_private_file(location.parent, location.name, create=False, write=False)
    except FileNotFoundError:
        return None
    try:
        size = os.fstat(descriptor).st_size
        if size < 1 or size > _IDENTITY_LIMIT:
            raise DayAgentVersionStoreError("version_store_metadata_invalid")
        payload = os.pread(descriptor, size, 0)
        if len(payload) != size:
            raise DayAgentVersionStoreError("version_store_metadata_invalid")
        return VersionStoreIdentity.model_validate_json(payload)
    finally:
        os.close(descriptor)


def _same_binding(left: VersionStoreIdentity, right: VersionStoreIdentity) -> bool:
    return (
        left.database == right.database
        and left.lock == right.lock
        and left.parent == right.parent
        and left.path == right.path
        and left.token == right.token
    )


def _matches_open_store(identity: VersionStoreIdentity, path: Path, opened: OpenVersionStore) -> bool:
    parent = os.fstat(opened.parent)
    database = os.fstat(opened.database)
    lock = os.fstat(opened.lock)
    return (
        identity.database == (database.st_dev, database.st_ino)
        and identity.lock == (lock.st_dev, lock.st_ino)
        and identity.parent == (parent.st_dev, parent.st_ino)
        and identity.path == str(path)
    )


def _is_pristine_bootstrap(path: Path, opened: OpenVersionStore, marker: IdentityLocation) -> bool:
    allowed = {
        marker.name,
        f"{marker.name}.{IdentityPhase.PREPARED.value}.tmp",
        f"{marker.name}.{IdentityPhase.COMMITTED.value}.tmp",
        path.name,
        f"{path.name}.writer.lock",
    }
    reserved = {
        name for name in os.listdir(opened.parent) if name.startswith(path.name) or name.startswith(marker.name)
    }
    return os.fstat(opened.database).st_size == 0 and reserved <= allowed


def _temporary_location(location: IdentityLocation, phase: IdentityPhase) -> IdentityLocation:
    return IdentityLocation(location.parent, f"{location.name}.{phase.value}.tmp")


def _require_named_identity(parent: int, name: str, descriptor: int) -> None:
    require_private_file(descriptor)
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise DayAgentVersionStoreError("version_store_metadata_invalid")


def _write_identity_file(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        offset += os.write(descriptor, payload[offset:])


def _sync_identity_file(descriptor: int) -> None:
    os.fsync(descriptor)


def _replace_identity_file(source: IdentityLocation, destination: IdentityLocation) -> None:
    os.replace(source.name, destination.name, src_dir_fd=source.parent, dst_dir_fd=destination.parent)


def _sync_identity_directory(descriptor: int) -> None:
    os.fsync(descriptor)


__all__ = ("OpenVersionStore", "require_version_store_identity")
