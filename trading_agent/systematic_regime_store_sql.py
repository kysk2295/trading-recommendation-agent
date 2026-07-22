from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
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
from trading_agent.systematic_regime_schema import (
    SYSTEMATIC_REGIME_EXPIRATION_SCHEMA_V2,
    SYSTEMATIC_REGIME_SCHEMA_V1,
    SYSTEMATIC_REGIME_SCHEMA_V2,
)
from trading_agent.systematic_regime_store_file import (
    InvalidSystematicRegimeFileError,
    load_sqlite_database,
    open_private_file,
    replace_sqlite_database,
    require_private_file,
)

_SCHEMA_VERSION: Final = 2


class InvalidSystematicRegimeSqliteError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime SQLite is invalid"


@contextmanager
def systematic_writer_connection(
    path: Path,
    *,
    create: bool = True,
) -> Iterator[sqlite3.Connection]:
    absolute = absolute_private_path(path)
    try:
        parent = open_private_parent(absolute.parent, create=create)
        try:
            require_private_directory(parent)
            lock_path = absolute.parent / f"{absolute.name}.writer.lock"
            if create:
                with _writer_lease(lock_path, parent, create=True):
                    database_descriptor = open_private_file(parent, absolute.name, create=True, write=True)
                    try:
                        with _opened_writer_database(
                            absolute,
                            parent,
                            database_descriptor,
                        ) as connection:
                            yield connection
                    finally:
                        os.close(database_descriptor)
            else:
                database_descriptor = open_private_file(parent, absolute.name, create=False, write=True)
                try:
                    with _writer_lease(lock_path, parent, create=False), _opened_writer_database(
                        absolute,
                        parent,
                        database_descriptor,
                    ) as connection:
                        yield connection
                finally:
                    os.close(database_descriptor)
        finally:
            os.close(parent)
    except (
        InvalidPrivateDirectoryIdentityError,
        InvalidSystematicRegimeFileError,
        OSError,
        sqlite3.Error,
        TypeError,
    ):
        raise InvalidSystematicRegimeSqliteError from None


@contextmanager
def _opened_writer_database(
    absolute: Path,
    parent: int,
    descriptor: int,
) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(":memory:")) as connection:
        _require_bound(absolute, parent, descriptor)
        original = load_sqlite_database(connection, descriptor)
        _enable_foreign_keys(connection)
        _prepare(connection)
        yield connection
        _require_bound(absolute, parent, descriptor)
        connection.commit()
        payload = connection.serialize()
        if payload != original:
            replace_sqlite_database(parent, absolute.name, payload)


@contextmanager
def systematic_reader_connection(path: Path) -> Iterator[sqlite3.Connection]:
    absolute = absolute_private_path(path)
    try:
        parent = open_private_parent(absolute.parent, create=False)
        try:
            require_private_directory_query_only(parent)
            descriptor = open_private_file(parent, absolute.name, create=False, write=False)
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
    except (
        InvalidPrivateDirectoryIdentityError,
        InvalidSystematicRegimeFileError,
        OSError,
        sqlite3.Error,
        TypeError,
    ):
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
def _writer_lease(path: Path, parent: int, *, create: bool) -> Iterator[None]:
    descriptor = open_private_file(parent, path.name, create=create, write=True)
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


def _require_bound(path: Path, parent: int, descriptor: int) -> None:
    require_open_directory_path(path.parent, parent)
    named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise InvalidSystematicRegimeSqliteError
    require_private_file(descriptor)


def _connect_descriptor(descriptor: int) -> sqlite3.Connection:
    return sqlite3.connect(
        f"file:/dev/fd/{descriptor}?mode=ro",
        uri=True,
        timeout=0.0,
    )


def _enable_foreign_keys(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
        raise InvalidSystematicRegimeSqliteError


def _prepare(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        if _signature(connection):
            raise InvalidSystematicRegimeSqliteError
        connection.executescript(
            f"{SYSTEMATIC_REGIME_SCHEMA_V2}PRAGMA user_version = {_SCHEMA_VERSION};"
        )
    elif version == (1,):
        if _signature(connection) != _EXPECTED_V1_SIGNATURE:
            raise InvalidSystematicRegimeSqliteError
        _require_integrity(connection)
        connection.executescript(
            f"{SYSTEMATIC_REGIME_EXPIRATION_SCHEMA_V2}"
            f"PRAGMA user_version = {_SCHEMA_VERSION};"
        )
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if (
        connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,)
        or _signature(connection) != _EXPECTED_SIGNATURE
    ):
        raise InvalidSystematicRegimeSqliteError
    _require_integrity(connection)


def _require_integrity(connection: sqlite3.Connection) -> None:
    if (
        connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
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


def _expected_signature(schema: str) -> tuple[tuple[str, str, str, str], ...]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(schema)
        return _signature(connection)


_EXPECTED_V1_SIGNATURE = _expected_signature(SYSTEMATIC_REGIME_SCHEMA_V1)
_EXPECTED_SIGNATURE = _expected_signature(SYSTEMATIC_REGIME_SCHEMA_V2)


__all__ = (
    "InvalidSystematicRegimeSqliteError",
    "private_store_exists",
    "systematic_reader_connection",
    "systematic_writer_connection",
)
