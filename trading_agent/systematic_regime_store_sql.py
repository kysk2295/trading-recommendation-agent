from __future__ import annotations

import fcntl
import os
import secrets
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from pathlib import Path
from typing import Final, override

from trading_agent.private_directory_identity import (
    InvalidPrivateDirectoryIdentityError,
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
    require_private_directory_query_only,
)

_SCHEMA_VERSION: Final = 1
_MAX_DATABASE_BYTES: Final = 64 * 1024 * 1024
_SCHEMA: Final = (
    "CREATE TABLE systematic_cards (card_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);"
    "CREATE TABLE systematic_card_publications ("
    "card_id TEXT PRIMARY KEY REFERENCES systematic_cards(card_id), payload_json TEXT NOT NULL);"
    "CREATE TABLE systematic_outcomes ("
    "card_id TEXT PRIMARY KEY REFERENCES systematic_cards(card_id), payload_json TEXT NOT NULL);"
    "CREATE TRIGGER systematic_cards_no_update BEFORE UPDATE ON systematic_cards "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    "CREATE TRIGGER systematic_cards_no_delete BEFORE DELETE ON systematic_cards "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    "CREATE TRIGGER systematic_card_publications_no_update BEFORE UPDATE "
    "ON systematic_card_publications BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    "CREATE TRIGGER systematic_card_publications_no_delete BEFORE DELETE "
    "ON systematic_card_publications BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    "CREATE TRIGGER systematic_outcomes_no_update BEFORE UPDATE ON systematic_outcomes "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    "CREATE TRIGGER systematic_outcomes_no_delete BEFORE DELETE ON systematic_outcomes "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
)


class InvalidSystematicRegimeSqliteError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime SQLite is invalid"


@contextmanager
def systematic_writer_connection(path: Path) -> Iterator[sqlite3.Connection]:
    absolute = absolute_private_path(path)
    try:
        parent = open_private_parent(absolute.parent, create=True)
        try:
            require_private_directory(parent)
            lock_path = absolute.parent / f"{absolute.name}.writer.lock"
            with _writer_lease(lock_path, parent):
                database_descriptor = _open_file(parent, absolute.name, create=True, write=True)
                try:
                    with closing(sqlite3.connect(":memory:")) as connection:
                        _require_bound(absolute, parent, database_descriptor)
                        original = _load_database(connection, database_descriptor)
                        _enable_foreign_keys(connection)
                        _prepare(connection)
                        yield connection
                        _require_bound(absolute, parent, database_descriptor)
                        connection.commit()
                        payload = connection.serialize()
                        if payload != original:
                            _replace_database(parent, absolute.name, payload)
                finally:
                    os.close(database_descriptor)
        finally:
            os.close(parent)
    except (InvalidPrivateDirectoryIdentityError, OSError, sqlite3.Error, TypeError):
        raise InvalidSystematicRegimeSqliteError from None


@contextmanager
def systematic_reader_connection(path: Path) -> Iterator[sqlite3.Connection]:
    absolute = absolute_private_path(path)
    try:
        parent = open_private_parent(absolute.parent, create=False)
        try:
            require_private_directory_query_only(parent)
            descriptor = _open_file(parent, absolute.name, create=False, write=False)
            try:
                with closing(_connect_descriptor(descriptor)) as connection:
                    _require_bound(absolute, parent, descriptor)
                    _enable_foreign_keys(connection)
                    _ = connection.execute("PRAGMA query_only = ON")
                    _require_schema(connection)
                    yield connection
                    _require_bound(absolute, parent, descriptor)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
    except (InvalidPrivateDirectoryIdentityError, OSError, sqlite3.Error, TypeError):
        raise InvalidSystematicRegimeSqliteError from None


def private_store_exists(path: Path) -> bool:
    absolute = absolute_private_path(path)
    try:
        metadata = absolute.lstat()
    except FileNotFoundError:
        return False
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidSystematicRegimeSqliteError
    return True


@contextmanager
def _writer_lease(path: Path, parent: int) -> Iterator[None]:
    descriptor = _open_file(parent, path.name, create=True, write=True)
    parent_locked = False
    locked = False
    try:
        _require_bound(path, parent, descriptor)
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        parent_locked = True
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    finally:
        try:
            _require_bound(path, parent, descriptor)
        finally:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            if parent_locked:
                fcntl.flock(parent, fcntl.LOCK_UN)
            os.close(descriptor)


def _open_file(parent: int, name: str, *, create: bool, write: bool) -> int:
    flags = os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    flags |= os.O_RDWR if write else os.O_RDONLY
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise
        descriptor = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
    try:
        _require_private_file(descriptor)
    except (OSError, ValueError):
        os.close(descriptor)
        raise
    return descriptor


def _require_bound(path: Path, parent: int, descriptor: int) -> None:
    require_open_directory_path(path.parent, parent)
    named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise InvalidSystematicRegimeSqliteError
    _require_private_file(descriptor)


def _require_private_file(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidSystematicRegimeSqliteError


def _connect_descriptor(descriptor: int) -> sqlite3.Connection:
    return sqlite3.connect(
        f"file:/dev/fd/{descriptor}?mode=ro",
        uri=True,
        timeout=0.0,
    )


def _load_database(connection: sqlite3.Connection, descriptor: int) -> bytes:
    size = os.fstat(descriptor).st_size
    if size > _MAX_DATABASE_BYTES:
        raise InvalidSystematicRegimeSqliteError
    payload = os.pread(descriptor, size, 0)
    if len(payload) != size:
        raise InvalidSystematicRegimeSqliteError
    if payload:
        connection.deserialize(payload)
    return payload


def _replace_database(parent: int, name: str, payload: bytes) -> None:
    temporary = f".{name}.{secrets.token_hex(16)}.tmp"
    descriptor = os.open(
        temporary,
        os.O_CLOEXEC | os.O_NOFOLLOW | os.O_RDWR | os.O_CREAT | os.O_EXCL,
        0o600,
        dir_fd=parent,
    )
    renamed = False
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        _require_private_file(descriptor)
        os.rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        renamed = True
        os.fsync(parent)
        replacement = _open_file(parent, name, create=False, write=False)
        os.close(replacement)
    finally:
        os.close(descriptor)
        if not renamed:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=parent)


def _enable_foreign_keys(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
        raise InvalidSystematicRegimeSqliteError


def _prepare(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() == (0,):
        if _signature(connection):
            raise InvalidSystematicRegimeSqliteError
        connection.executescript(f"{_SCHEMA}PRAGMA user_version = {_SCHEMA_VERSION};")
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if (
        connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,)
        or _signature(connection) != _EXPECTED_SIGNATURE
        or connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
        or connection.execute("PRAGMA foreign_key_check").fetchall()
    ):
        raise InvalidSystematicRegimeSqliteError


def _signature(connection: sqlite3.Connection) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    )


def _expected_signature() -> tuple[tuple[str, str, str, str], ...]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(_SCHEMA)
        return _signature(connection)


_EXPECTED_SIGNATURE = _expected_signature()


__all__ = (
    "InvalidSystematicRegimeSqliteError",
    "private_store_exists",
    "systematic_reader_connection",
    "systematic_writer_connection",
)
