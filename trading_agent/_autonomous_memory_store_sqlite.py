from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.private_directory_identity import (
    open_private_parent,
    require_open_directory_path,
    require_private_directory_query_only,
)
from trading_agent.systematic_regime_store_file import (
    InvalidSystematicRegimeFileError,
    load_sqlite_database,
    open_private_file,
    replace_sqlite_database,
    require_private_file,
)

_SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE autonomous_memories (
  memory_id TEXT PRIMARY KEY,
  memory_key TEXT NOT NULL,
  version INTEGER NOT NULL,
  scope TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(memory_key, version)
);
CREATE TRIGGER autonomous_memories_no_update BEFORE UPDATE ON autonomous_memories
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER autonomous_memories_no_delete BEFORE DELETE ON autonomous_memories
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""


class AutonomousMemoryStoreError(ValueError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class AutonomousMemoryConflictError(AutonomousMemoryStoreError):
    pass


class InvalidAutonomousMemoryStoreError(AutonomousMemoryStoreError):
    pass


@dataclass(slots=True)
class DatabaseIdentity:
    parent: int
    name: str
    descriptor: int
    path: Path


def _schema_rows() -> tuple[tuple[str, str, str, str], ...]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(_SCHEMA)
        return tuple(
            connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )


_EXPECTED_SCHEMA: Final = _schema_rows()


def open_database(parent: int, name: str, *, create: bool, write: bool) -> int:
    try:
        return open_private_file(parent, name, create=create, write=write)
    except FileNotFoundError:
        raise
    except (InvalidSystematicRegimeFileError, OSError, ValueError) as error:
        raise InvalidAutonomousMemoryStoreError(reason="database_path_invalid") from error


@contextmanager
def writer_lease(path: Path, parent: int) -> Iterator[None]:
    descriptor = open_database(parent, f"{path.name}.writer.lock", create=True, write=True)
    parent_locked = False
    locked = False
    try:
        require_open_directory_path(path.parent, parent)
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        parent_locked = True
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        if parent_locked:
            fcntl.flock(parent, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def reader_connection(path: Path) -> Iterator[sqlite3.Connection]:
    parent = open_private_parent(path.parent, create=False)
    descriptor = -1
    try:
        require_private_directory_query_only(parent)
        require_open_directory_path(path.parent, parent)
        descriptor = open_database(parent, path.name, create=False, write=False)
        identity = DatabaseIdentity(parent, path.name, descriptor, path)
        with closing(sqlite3.connect(f"file:/dev/fd/{descriptor}?mode=ro", uri=True, timeout=0.0)) as connection:
            require_identity(identity)
            _ = connection.execute("PRAGMA query_only = ON")
            require_schema(connection)
            yield connection
            require_snapshot(identity)
        require_open_directory_path(path.parent, parent)
    except FileNotFoundError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        if isinstance(error, AutonomousMemoryStoreError):
            raise
        raise InvalidAutonomousMemoryStoreError(reason="database_read_failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


@contextmanager
def writer_connection(identity: DatabaseIdentity) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(":memory:")) as connection:
        require_identity(identity)
        original = load_sqlite_database(connection, identity.descriptor)
        prepare_writer(connection)
        if connection.serialize() != original:
            flush_generation(identity, connection)
        yield connection
        require_identity(identity)


def flush_generation(identity: DatabaseIdentity, connection: sqlite3.Connection) -> None:
    require_identity(identity)
    payload = connection.serialize()
    replace_sqlite_database(identity.parent, identity.name, payload)
    replacement = open_database(identity.parent, identity.name, create=False, write=True)
    original = identity.descriptor
    try:
        if os.fstat(replacement).st_size != len(payload) or os.pread(replacement, len(payload), 0) != payload:
            raise OSError
        identity.descriptor = replacement
        require_identity(identity)
    except (OSError, ValueError):
        identity.descriptor = original
        os.close(replacement)
        raise
    os.close(original)


def reconcile_generation(identity: DatabaseIdentity, connection: sqlite3.Connection) -> None:
    replacement = open_database(identity.parent, identity.name, create=False, write=True)
    original = identity.descriptor
    try:
        identity.descriptor = replacement
        require_identity(identity)
        _ = load_sqlite_database(connection, replacement)
        require_schema(connection)
    except (OSError, ValueError, sqlite3.Error):
        identity.descriptor = original
        os.close(replacement)
        raise
    os.close(original)


def prepare_writer(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        objects = tuple(connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
        if objects:
            raise InvalidAutonomousMemoryStoreError(reason="schema_version_invalid")
        connection.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={_SCHEMA_VERSION};COMMIT;")
    require_schema(connection)


def require_schema(connection: sqlite3.Connection) -> None:
    rows = tuple(
        connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,) or rows != _EXPECTED_SCHEMA:
        raise InvalidAutonomousMemoryStoreError(reason="schema_objects_invalid")


def require_identity(identity: DatabaseIdentity) -> None:
    require_open_directory_path(identity.path.parent, identity.parent)
    named = os.stat(identity.name, dir_fd=identity.parent, follow_symlinks=False)
    opened = os.fstat(identity.descriptor)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise OSError
    require_private_file(identity.descriptor)


def require_snapshot(identity: DatabaseIdentity) -> None:
    require_open_directory_path(identity.path.parent, identity.parent)
    metadata = os.fstat(identity.descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink not in {0, 1}
    ):
        raise OSError
