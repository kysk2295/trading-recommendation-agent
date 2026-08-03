from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.private_directory_identity import (
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
    require_private_directory_query_only,
    require_same_file,
)

_APPLICATION_ID: Final = 0x534F414B
_SCHEMA: Final = """
CREATE TABLE checkpoints (
  sequence INTEGER PRIMARY KEY,
  payload_json TEXT NOT NULL UNIQUE,
  checkpoint_sha256 TEXT NOT NULL UNIQUE
);
CREATE TRIGGER checkpoints_no_update BEFORE UPDATE ON checkpoints
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER checkpoints_no_delete BEFORE DELETE ON checkpoints
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_EXACT_SCHEMA: Final = (
    (
        "table",
        "checkpoints",
        "CREATE TABLE checkpoints (\n"
        "  sequence INTEGER PRIMARY KEY,\n"
        "  payload_json TEXT NOT NULL UNIQUE,\n"
        "  checkpoint_sha256 TEXT NOT NULL UNIQUE\n)",
    ),
    (
        "trigger",
        "checkpoints_no_delete",
        "CREATE TRIGGER checkpoints_no_delete BEFORE DELETE ON checkpoints\n"
        "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    ),
    (
        "trigger",
        "checkpoints_no_update",
        "CREATE TRIGGER checkpoints_no_update BEFORE UPDATE ON checkpoints\n"
        "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    ),
)


@dataclass(frozen=True, slots=True)
class _DatabaseIdentity:
    parent: int
    name: str
    descriptor: int
    path: Path


@contextmanager
def create_soak_database(path: Path) -> Iterator[sqlite3.Connection]:
    parent = open_private_parent(path.parent, create=True)
    descriptor = -1
    completed = False
    try:
        require_private_directory(parent)
        require_open_directory_path(path.parent, parent)
        descriptor = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        identity = _DatabaseIdentity(parent=parent, name=path.name, descriptor=descriptor, path=path)
        with closing(_connect_database(identity, write=True)) as connection:
            yield connection
            _require_path_identity(parent, path.name, descriptor)
        _require_path_identity(parent, path.name, descriptor)
        require_open_directory_path(path.parent, parent)
        completed = True
    finally:
        if descriptor >= 0 and not completed:
            _unlink_same_identity(parent, path.name, descriptor)
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


@contextmanager
def open_soak_database(path: Path, *, write: bool) -> Iterator[sqlite3.Connection]:
    parent = open_private_parent(path.parent, create=False)
    descriptor = -1
    try:
        require_private_directory_query_only(parent)
        require_open_directory_path(path.parent, parent)
        flags = os.O_RDWR if write else os.O_RDONLY | os.O_NONBLOCK
        descriptor = os.open(path.name, flags | os.O_NOFOLLOW, dir_fd=parent)
        _require_private_descriptor(descriptor)
        identity = _DatabaseIdentity(parent=parent, name=path.name, descriptor=descriptor, path=path)
        with closing(_connect_database(identity, write=write)) as connection:
            yield connection
            _require_path_identity(parent, path.name, descriptor)
        _require_path_identity(parent, path.name, descriptor)
        require_open_directory_path(path.parent, parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA)
    _ = connection.execute("PRAGMA user_version=1")
    _ = connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
    connection.commit()
    require_schema(connection)


def require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (1,):
        raise sqlite3.DatabaseError
    if connection.execute("PRAGMA application_id").fetchone() != (_APPLICATION_ID,):
        raise sqlite3.DatabaseError
    integrity: list[tuple[str]] = connection.execute("PRAGMA integrity_check").fetchall()
    schema: tuple[tuple[str, str, str], ...] = tuple(
        connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    )
    foreign_keys: list[tuple[int, str, str, int]] = connection.execute("PRAGMA foreign_key_check").fetchall()
    if integrity != [("ok",)] or schema != _EXACT_SCHEMA or foreign_keys:
        raise sqlite3.DatabaseError


def _connect_database(identity: _DatabaseIdentity, *, write: bool) -> sqlite3.Connection:
    require_open_directory_path(identity.path.parent, identity.parent)
    _require_path_identity(identity.parent, identity.name, identity.descriptor)
    connection = sqlite3.connect(identity.path, timeout=0.0)
    require_open_directory_path(identity.path.parent, identity.parent)
    _require_path_identity(identity.parent, identity.name, identity.descriptor)
    if not write:
        _ = connection.execute("PRAGMA query_only=ON")
    return connection


def _require_path_identity(parent: int, name: str, expected: int) -> None:
    current = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        require_same_file(expected, current)
    finally:
        os.close(current)


def _unlink_same_identity(parent: int, name: str, expected: int) -> None:
    with suppress(OSError, ValueError):
        current = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            require_same_file(expected, current)
            os.unlink(name, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(current)


def _require_private_descriptor(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise OSError


__all__ = ("create_soak_database", "initialize_schema", "open_soak_database", "require_schema")
