from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from trading_agent.private_directory_identity import (
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
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
CREATE TABLE autonomous_tasks (
  task_id TEXT PRIMARY KEY,
  root_source_evidence_id TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE autonomous_task_steps (
  step_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES autonomous_tasks(task_id),
  sequence INTEGER NOT NULL,
  occurred_at TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(task_id, sequence)
);
CREATE TRIGGER autonomous_tasks_no_update BEFORE UPDATE ON autonomous_tasks
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER autonomous_tasks_no_delete BEFORE DELETE ON autonomous_tasks
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER autonomous_task_steps_no_update BEFORE UPDATE ON autonomous_task_steps
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER autonomous_task_steps_no_delete BEFORE DELETE ON autonomous_task_steps
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_SCHEMA_OBJECTS: Final = frozenset(
    {
        "autonomous_tasks",
        "autonomous_task_steps",
        "autonomous_tasks_no_update",
        "autonomous_tasks_no_delete",
        "autonomous_task_steps_no_update",
        "autonomous_task_steps_no_delete",
    }
)


class AutonomousTaskStoreError(ValueError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @override
    def __str__(self) -> str:
        return self.reason


class AutonomousTaskConflictError(AutonomousTaskStoreError):
    pass


class InvalidAutonomousTaskStoreError(AutonomousTaskStoreError):
    pass


@dataclass(slots=True)
class _DatabaseIdentity:
    parent: int
    name: str
    descriptor: int
    path: Path


def _expected_schema_rows() -> tuple[tuple[str, str, str, str], ...]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(_SCHEMA)
        return tuple(
            connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        )


_SCHEMA_ROWS: Final = _expected_schema_rows()


def _open_private_parent(path: Path, *, create: bool) -> int:
    return open_private_parent(path, create=create)


def _require_private_directory(descriptor: int) -> None:
    require_private_directory(descriptor)


def _require_open_directory_path(path: Path, descriptor: int) -> None:
    require_open_directory_path(path, descriptor)


@contextmanager
def _reader_connection(path: Path) -> Iterator[sqlite3.Connection]:
    parent = -1
    descriptor = -1
    try:
        parent = open_private_parent(path.parent, create=False)
        require_private_directory_query_only(parent)
        require_open_directory_path(path.parent, parent)
        descriptor = _open_private_database(parent, path.name, create=False, write=False)
        identity = _DatabaseIdentity(parent, path.name, descriptor, path)
        with closing(_connect_descriptor(descriptor)) as connection:
            _require_database_identity(identity)
            _enable_foreign_keys(connection)
            _ = connection.execute("PRAGMA query_only = ON")
            _require_schema(connection)
            yield connection
            _require_reader_snapshot(identity)
        _require_reader_snapshot(identity)
        require_open_directory_path(path.parent, parent)
    except FileNotFoundError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        if isinstance(error, AutonomousTaskStoreError):
            raise
        raise InvalidAutonomousTaskStoreError(reason="database_read_failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)


def _open_private_database(parent: int, name: str, *, create: bool, write: bool) -> int:
    try:
        return open_private_file(parent, name, create=create, write=write)
    except FileNotFoundError:
        raise
    except (InvalidSystematicRegimeFileError, OSError, ValueError) as error:
        raise InvalidAutonomousTaskStoreError(reason="database_path_invalid") from error


@contextmanager
def _writer_lease(path: Path, parent: int) -> Iterator[None]:
    descriptor = _open_private_database(parent, f"{path.name}.writer.lock", create=True, write=True)
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
def _writer_connection(identity: _DatabaseIdentity) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(":memory:")) as connection:
        _require_database_identity(identity)
        original = load_sqlite_database(connection, identity.descriptor)
        _enable_foreign_keys(connection)
        _prepare_writer_connection(connection)
        if connection.serialize() != original:
            _flush_writer_generation(identity, connection)
        yield connection
        _require_database_identity(identity)


def _flush_writer_generation(identity: _DatabaseIdentity, connection: sqlite3.Connection) -> None:
    _require_database_identity(identity)
    payload = connection.serialize()
    replace_sqlite_database(identity.parent, identity.name, payload)
    replacement = _open_private_database(identity.parent, identity.name, create=False, write=True)
    original = identity.descriptor
    adopted = False
    try:
        _require_generation_payload(replacement, payload)
        identity.descriptor = replacement
        _require_database_identity(identity)
        adopted = True
    finally:
        if adopted:
            os.close(original)
        else:
            identity.descriptor = original
            os.close(replacement)


def _require_generation_payload(descriptor: int, payload: bytes) -> None:
    if os.fstat(descriptor).st_size != len(payload) or os.pread(descriptor, len(payload), 0) != payload:
        raise OSError


def _reconcile_writer_generation(identity: _DatabaseIdentity, connection: sqlite3.Connection) -> None:
    replacement = _open_private_database(identity.parent, identity.name, create=False, write=True)
    original = identity.descriptor
    adopted = False
    try:
        identity.descriptor = replacement
        _require_database_identity(identity)
        _ = load_sqlite_database(connection, replacement)
        _enable_foreign_keys(connection)
        _require_schema(connection)
        adopted = True
    finally:
        if adopted:
            os.close(original)
        else:
            identity.descriptor = original
            os.close(replacement)


def _connect_descriptor(descriptor: int) -> sqlite3.Connection:
    return sqlite3.connect(f"file:/dev/fd/{descriptor}?mode=ro", uri=True, timeout=0.0)


def _enable_foreign_keys(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
        raise sqlite3.DatabaseError


def _require_database_identity(identity: _DatabaseIdentity) -> None:
    require_open_directory_path(identity.path.parent, identity.parent)
    named = os.stat(identity.name, dir_fd=identity.parent, follow_symlinks=False)
    opened = os.fstat(identity.descriptor)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise OSError
    require_private_file(identity.descriptor)


def _require_reader_snapshot(identity: _DatabaseIdentity) -> None:
    require_open_directory_path(identity.path.parent, identity.parent)
    metadata = os.fstat(identity.descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink not in {0, 1}
    ):
        raise OSError


def _prepare_writer_connection(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    version = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        objects = frozenset(
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
        )
        if objects:
            raise InvalidAutonomousTaskStoreError(reason="schema_version_invalid")
        connection.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={_SCHEMA_VERSION};COMMIT;")
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
        raise InvalidAutonomousTaskStoreError(reason="schema_version_invalid")
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    )
    if objects != _SCHEMA_OBJECTS:
        raise InvalidAutonomousTaskStoreError(reason="schema_objects_invalid")
    rows = tuple(
        connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    )
    if rows != _SCHEMA_ROWS:
        raise InvalidAutonomousTaskStoreError(reason="schema_objects_invalid")
