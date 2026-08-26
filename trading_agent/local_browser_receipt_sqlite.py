from __future__ import annotations

import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.local_browser_private_fs import open_private_browser_directory

_SCHEMA_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class PrivateBrowserReceiptDatabase:
    connection: sqlite3.Connection
    identity_descriptor: int

    def close(self) -> None:
        self.connection.close()
        os.close(self.identity_descriptor)


def open_private_browser_receipt_database(path: Path, owner_id: int) -> PrivateBrowserReceiptDatabase:
    with open_private_browser_directory(path.parent, owner_id) as parent:
        descriptor = _open_database_entry(parent.descriptor, path.name, owner_id)
        try:
            connection = sqlite3.connect(path, timeout=0.0)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            os.close(descriptor)
            raise
        try:
            _require_database_identity(parent.descriptor, path.name, descriptor)
            _prepare_connection(connection)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            connection.close()
            os.close(descriptor)
            raise
    return PrivateBrowserReceiptDatabase(connection, descriptor)


def _open_database_entry(parent: int, name: str, owner_id: int) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
    except FileExistsError:
        descriptor = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
    metadata = os.fstat(descriptor)
    if not _private_database(metadata, owner_id):
        os.close(descriptor)
        raise OSError
    return descriptor


def _require_database_identity(parent: int, name: str, descriptor: int) -> None:
    path_metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
    descriptor_metadata = os.fstat(descriptor)
    if not _private_database(path_metadata, descriptor_metadata.st_uid) or (
        path_metadata.st_dev,
        path_metadata.st_ino,
    ) != (descriptor_metadata.st_dev, descriptor_metadata.st_ino):
        raise OSError


def _private_database(metadata: os.stat_result, owner_id: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == owner_id
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def _prepare_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA temp_store = MEMORY")
    version = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(_SCHEMA)
    elif version != (_SCHEMA_VERSION,):
        raise sqlite3.DatabaseError
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    request_columns = tuple(
        row[1] for row in connection.execute("PRAGMA table_info(local_browser_requests)").fetchall()
    )
    response_columns = tuple(
        row[1] for row in connection.execute("PRAGMA table_info(local_browser_responses)").fetchall()
    )
    if request_columns != (
        "request_id",
        "action",
        "request_sha256",
        "target_id",
        "normalized_url",
        "occurred_at",
    ) or response_columns != (
        "request_id",
        "response_json",
        "response_sha256",
        "status",
        "reason",
        "target_id",
        "normalized_url",
        "observation_sha256",
        "screenshot_sha256",
        "occurred_at",
    ):
        raise sqlite3.DatabaseError
    triggers = {
        row[0]: " ".join(str(row[1]).split())
        for row in connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall()
    }
    if triggers != _EXPECTED_TRIGGERS:
        raise sqlite3.DatabaseError


_SCHEMA: Final = """
CREATE TABLE local_browser_requests (
 request_id TEXT PRIMARY KEY, action TEXT NOT NULL, request_sha256 TEXT NOT NULL,
 target_id TEXT, normalized_url TEXT, occurred_at TEXT NOT NULL);
CREATE TABLE local_browser_responses (
 request_id TEXT PRIMARY KEY REFERENCES local_browser_requests(request_id),
 response_json TEXT NOT NULL CHECK(length(response_json) <= 16384), response_sha256 TEXT NOT NULL,
 status TEXT NOT NULL, reason TEXT, target_id TEXT, normalized_url TEXT,
 observation_sha256 TEXT, screenshot_sha256 TEXT, occurred_at TEXT NOT NULL);
CREATE TRIGGER local_browser_requests_no_update BEFORE UPDATE ON local_browser_requests
 BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER local_browser_requests_no_delete BEFORE DELETE ON local_browser_requests
 BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER local_browser_responses_no_update BEFORE UPDATE ON local_browser_responses
 BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER local_browser_responses_no_delete BEFORE DELETE ON local_browser_responses
 BEGIN SELECT RAISE(ABORT, 'append-only'); END;
PRAGMA user_version = 1;
"""

_EXPECTED_TRIGGERS: Final = {
    "local_browser_requests_no_update": "CREATE TRIGGER local_browser_requests_no_update BEFORE UPDATE ON "
    "local_browser_requests BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "local_browser_requests_no_delete": "CREATE TRIGGER local_browser_requests_no_delete BEFORE DELETE ON "
    "local_browser_requests BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "local_browser_responses_no_update": "CREATE TRIGGER local_browser_responses_no_update BEFORE UPDATE ON "
    "local_browser_responses BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "local_browser_responses_no_delete": "CREATE TRIGGER local_browser_responses_no_delete BEFORE DELETE ON "
    "local_browser_responses BEGIN SELECT RAISE(ABORT, 'append-only'); END",
}
