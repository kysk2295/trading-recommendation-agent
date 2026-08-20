from __future__ import annotations

import fcntl
import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.day_agent_version_models import DayAgentVersionStoreError
from trading_agent.private_directory_identity import (
    open_private_parent,
    require_open_directory_path,
    require_private_directory_query_only,
)
from trading_agent.systematic_regime_store_file import open_private_file, require_private_file

_IDENTITY_SUFFIX: Final = "day-agent-version-store.json"
_IDENTITY_LIMIT: Final = 4_096

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_versions (
  version_id TEXT PRIMARY KEY,
  deployment_state TEXT NOT NULL,
  parent_version_id TEXT,
  payload_json TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_initial_champion
  ON agent_versions(deployment_state) WHERE deployment_state = 'champion';
CREATE TABLE IF NOT EXISTS change_proposals (
  proposal_id TEXT PRIMARY KEY,
  version_id TEXT NOT NULL REFERENCES agent_versions(version_id),
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS promotion_recommendations (
  recommendation_id TEXT PRIMARY KEY,
  challenger_version_id TEXT NOT NULL REFERENCES agent_versions(version_id),
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deployment_transitions (
  transition_id TEXT PRIMARY KEY,
  recommendation_id TEXT NOT NULL UNIQUE REFERENCES promotion_recommendations(recommendation_id),
  demoted_version_id TEXT NOT NULL REFERENCES agent_versions(version_id),
  promoted_version_id TEXT NOT NULL REFERENCES agent_versions(version_id),
  payload_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS agent_versions_no_update BEFORE UPDATE ON agent_versions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS agent_versions_no_delete BEFORE DELETE ON agent_versions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS change_proposals_no_update BEFORE UPDATE ON change_proposals
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS change_proposals_no_delete BEFORE DELETE ON change_proposals
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS promotion_recommendations_no_update
BEFORE UPDATE ON promotion_recommendations BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS promotion_recommendations_no_delete
BEFORE DELETE ON promotion_recommendations BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS deployment_transitions_no_update
BEFORE UPDATE ON deployment_transitions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS deployment_transitions_no_delete
BEFORE DELETE ON deployment_transitions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""


@dataclass(frozen=True, slots=True)
class OpenVersionStore:
    parent: int
    database: int
    lock: int


def open_version_store_writer(path: Path) -> OpenVersionStore:
    parent = -1
    database = -1
    lock = -1
    parent_locked = False
    lock_locked = False
    try:
        parent = open_private_parent(path.parent, create=True)
        require_private_directory_query_only(parent)
        require_open_directory_path(path.parent, parent)
        lock = open_private_file(parent, f"{path.name}.writer.lock", create=True, write=True)
        try:
            fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
            parent_locked = True
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_locked = True
        except BlockingIOError:
            raise DayAgentVersionStoreError("version_store_writer_busy") from None
        database = open_private_file(parent, path.name, create=True, write=True)
        opened = OpenVersionStore(parent, database, lock)
        require_open_version_store(path, opened, initialize=True)
        return opened
    except DayAgentVersionStoreError:
        _close_opened(parent, database, lock, parent_locked, lock_locked)
        raise
    except (OSError, TypeError, ValueError):
        _close_opened(parent, database, lock, parent_locked, lock_locked)
        raise DayAgentVersionStoreError("version_store_metadata_invalid") from None


def close_version_store_writer(path: Path, opened: OpenVersionStore) -> None:
    try:
        require_open_version_store(path, opened, initialize=False)
    finally:
        _close_opened(opened.parent, opened.database, opened.lock, True, True)


def require_persisted_version_store(path: Path) -> None:
    parent = -1
    database = -1
    lock = -1
    try:
        parent = open_private_parent(path.parent, create=False)
        require_private_directory_query_only(parent)
        require_open_directory_path(path.parent, parent)
        database = open_private_file(parent, path.name, create=False, write=False)
        lock = open_private_file(parent, f"{path.name}.writer.lock", create=False, write=False)
        require_open_version_store(path, OpenVersionStore(parent, database, lock), initialize=False)
    except FileNotFoundError:
        raise
    except (OSError, TypeError, ValueError):
        raise DayAgentVersionStoreError("version_store_metadata_invalid") from None
    finally:
        for descriptor in (database, lock, parent):
            if descriptor >= 0:
                os.close(descriptor)


def require_open_version_store(path: Path, opened: OpenVersionStore, *, initialize: bool) -> None:
    require_open_directory_path(path.parent, opened.parent)
    _require_named_identity(opened.parent, path.name, opened.database)
    _require_named_identity(opened.parent, f"{path.name}.writer.lock", opened.lock)
    marker_name = f".{path.name}.{_IDENTITY_SUFFIX}"
    anchor_parent = open_private_parent(path.parent.parent, create=False)
    try:
        anchor_name = f".{path.parent.name}.{path.name}.{_IDENTITY_SUFFIX}"
        marker = _read_identity(opened.parent, marker_name)
        anchor = _read_identity(anchor_parent, anchor_name)
        if marker is None and anchor is None and initialize:
            token = secrets.token_hex(32)
            payload = _identity_payload(path, opened, token)
            _publish_identity(anchor_parent, anchor_name, payload)
            _publish_identity(opened.parent, marker_name, payload)
            return
        if marker is None or anchor is None or marker != anchor:
            raise DayAgentVersionStoreError("version_store_metadata_invalid")
        decoded = json.loads(anchor)
        if decoded != json.loads(_identity_payload(path, opened, str(decoded["token"]))):
            raise DayAgentVersionStoreError("version_store_metadata_invalid")
    except (KeyError, json.JSONDecodeError, OSError, TypeError, ValueError):
        raise DayAgentVersionStoreError("version_store_metadata_invalid") from None
    finally:
        os.close(anchor_parent)


def _identity_payload(path: Path, opened: OpenVersionStore, token: str) -> str:
    parent = os.fstat(opened.parent)
    database = os.fstat(opened.database)
    lock = os.fstat(opened.lock)
    return json.dumps(
        {
            "database": [database.st_dev, database.st_ino],
            "lock": [lock.st_dev, lock.st_ino],
            "parent": [parent.st_dev, parent.st_ino],
            "path": str(path),
            "token": token,
            "version": 1,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _require_named_identity(parent: int, name: str, descriptor: int) -> None:
    require_private_file(descriptor)
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise DayAgentVersionStoreError("version_store_metadata_invalid")


def _read_identity(parent: int, name: str) -> str | None:
    try:
        descriptor = open_private_file(parent, name, create=False, write=False)
    except FileNotFoundError:
        return None
    try:
        size = os.fstat(descriptor).st_size
        if size < 1 or size > _IDENTITY_LIMIT:
            raise DayAgentVersionStoreError("version_store_metadata_invalid")
        payload = os.pread(descriptor, size, 0)
        if len(payload) != size:
            raise DayAgentVersionStoreError("version_store_metadata_invalid")
        return payload.decode()
    finally:
        os.close(descriptor)


def _publish_identity(parent: int, name: str, payload: str) -> None:
    descriptor = open_private_file(parent, name, create=True, write=True)
    try:
        if os.fstat(descriptor).st_size != 0:
            raise DayAgentVersionStoreError("version_store_metadata_invalid")
        encoded = payload.encode()
        if os.write(descriptor, encoded) != len(encoded):
            raise DayAgentVersionStoreError("version_store_metadata_invalid")
        os.fsync(descriptor)
        os.fsync(parent)
    finally:
        os.close(descriptor)


def _close_opened(
    parent: int,
    database: int,
    lock: int,
    parent_locked: bool,
    lock_locked: bool,
) -> None:
    if lock_locked:
        fcntl.flock(lock, fcntl.LOCK_UN)
    if parent_locked:
        fcntl.flock(parent, fcntl.LOCK_UN)
    for descriptor in (database, lock, parent):
        if descriptor >= 0:
            os.close(descriptor)


def current_champion_id(connection: sqlite3.Connection) -> str | None:
    latest = connection.execute(
        "SELECT promoted_version_id FROM deployment_transitions ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if latest is not None:
        return latest[0]
    initial = connection.execute("SELECT version_id FROM agent_versions WHERE deployment_state='champion'").fetchone()
    return None if initial is None else initial[0]


__all__ = (
    "SCHEMA",
    "OpenVersionStore",
    "close_version_store_writer",
    "current_champion_id",
    "open_version_store_writer",
    "require_persisted_version_store",
)
