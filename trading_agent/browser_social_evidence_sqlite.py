from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.local_browser_private_fs import (
    InvalidLocalBrowserPrivateFsError,
    open_private_browser_directory,
)

_SCHEMA_VERSION: Final = 1


class InvalidPrivateBrowserSocialEvidenceDatabaseError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self) -> None:
        self.reason = "browser_social_database_invalid"
        super().__init__(self.reason)

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class PrivateBrowserSocialEvidenceDatabase:
    connection: sqlite3.Connection
    identity_descriptor: int
    path: Path
    owner_id: int

    def close(self) -> None:
        self.connection.close()
        os.close(self.identity_descriptor)

    def require_current(self) -> None:
        try:
            with open_private_browser_directory(self.path.parent, self.owner_id) as parent:
                _require_database_identity(
                    _PrivateDatabaseEntry(
                        parent.descriptor,
                        self.path.name,
                        self.identity_descriptor,
                        self.owner_id,
                    )
                )
        except (InvalidLocalBrowserPrivateFsError, OSError, TypeError, ValueError):
            raise InvalidPrivateBrowserSocialEvidenceDatabaseError() from None


@contextmanager
def open_private_browser_social_evidence_database(
    path: Path, owner_id: int
) -> Iterator[PrivateBrowserSocialEvidenceDatabase]:
    database = _open_database(path, owner_id)
    try:
        yield database
    finally:
        try:
            database.require_current()
        finally:
            database.close()


def _open_database(path: Path, owner_id: int) -> PrivateBrowserSocialEvidenceDatabase:
    descriptor: int | None = None
    connection: sqlite3.Connection | None = None
    try:
        with open_private_browser_directory(path.parent, owner_id) as parent:
            descriptor = _open_database_entry(parent.descriptor, path.name, owner_id)
            connection = sqlite3.connect(path, timeout=5.0)
            entry = _PrivateDatabaseEntry(parent.descriptor, path.name, descriptor, owner_id)
            _require_database_identity(entry)
            _prepare_connection(connection)
            _require_database_identity(entry)
        return PrivateBrowserSocialEvidenceDatabase(connection, descriptor, path, owner_id)
    except (
        InvalidLocalBrowserPrivateFsError,
        InvalidPrivateBrowserSocialEvidenceDatabaseError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ):
        if connection is not None:
            connection.close()
        if descriptor is not None:
            os.close(descriptor)
        raise InvalidPrivateBrowserSocialEvidenceDatabaseError() from None


def _open_database_entry(parent: int, name: str, owner_id: int) -> int:
    if not name or name in {".", ".."}:
        raise InvalidPrivateBrowserSocialEvidenceDatabaseError()
    created = False
    try:
        descriptor = os.open(
            name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent,
        )
        created = True
    except FileExistsError:
        descriptor = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        if created:
            os.fchmod(descriptor, 0o600)
        if not _private_database(os.fstat(descriptor), owner_id):
            raise InvalidPrivateBrowserSocialEvidenceDatabaseError()
    except (InvalidPrivateBrowserSocialEvidenceDatabaseError, OSError):
        os.close(descriptor)
        raise
    return descriptor


@dataclass(frozen=True, slots=True)
class _PrivateDatabaseEntry:
    parent_descriptor: int
    name: str
    identity_descriptor: int
    owner_id: int


def _require_database_identity(entry: _PrivateDatabaseEntry) -> None:
    path_metadata = os.stat(entry.name, dir_fd=entry.parent_descriptor, follow_symlinks=False)
    descriptor_metadata = os.fstat(entry.identity_descriptor)
    identities_match = (path_metadata.st_dev, path_metadata.st_ino) == (
        descriptor_metadata.st_dev,
        descriptor_metadata.st_ino,
    )
    if (
        not _private_database(path_metadata, entry.owner_id)
        or not _private_database(descriptor_metadata, entry.owner_id)
        or not identities_match
    ):
        raise InvalidPrivateBrowserSocialEvidenceDatabaseError()


def _private_database(metadata: os.stat_result, owner_id: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == owner_id
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def _prepare_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("BEGIN IMMEDIATE")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()
        if version == (0,):
            for statement in _SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute("PRAGMA user_version=1")
        elif version != (_SCHEMA_VERSION,):
            raise InvalidPrivateBrowserSocialEvidenceDatabaseError()
        _require_schema(connection)
        connection.commit()
    except (InvalidPrivateBrowserSocialEvidenceDatabaseError, sqlite3.Error):
        connection.rollback()
        raise


def _require_schema(connection: sqlite3.Connection) -> None:
    objects = {
        (str(row[0]), str(row[1])): " ".join(str(row[2]).split())
        for row in connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    if objects != _EXPECTED_SCHEMA:
        raise InvalidPrivateBrowserSocialEvidenceDatabaseError()


_SCHEMA_STATEMENTS: Final = (
    """CREATE TABLE browser_social_evidence (
    evidence_id TEXT PRIMARY KEY,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    payload_json TEXT NOT NULL CHECK(length(payload_json)<=65536),
    captured_at TEXT NOT NULL,
    title TEXT NOT NULL CHECK(length(title)<=500),
    author_label TEXT NOT NULL CHECK(length(author_label)<=200),
    excerpt TEXT NOT NULL CHECK(length(excerpt) BETWEEN 1 AND 2000),
    normalized_url TEXT NOT NULL CHECK(length(normalized_url) BETWEEN 8 AND 2048))""",
    """CREATE TRIGGER browser_social_evidence_no_update
    BEFORE UPDATE ON browser_social_evidence
    BEGIN SELECT RAISE(ABORT, 'append-only'); END""",
    """CREATE TRIGGER browser_social_evidence_no_delete
    BEFORE DELETE ON browser_social_evidence
    BEGIN SELECT RAISE(ABORT, 'append-only'); END""",
)

_EXPECTED_SCHEMA: Final = {
    ("table", "browser_social_evidence"): " ".join(_SCHEMA_STATEMENTS[0].split()),
    ("trigger", "browser_social_evidence_no_update"): " ".join(_SCHEMA_STATEMENTS[1].split()),
    ("trigger", "browser_social_evidence_no_delete"): " ".join(_SCHEMA_STATEMENTS[2].split()),
}
