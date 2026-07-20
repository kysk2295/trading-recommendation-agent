from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from trading_agent.private_directory_identity import (
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
    require_private_directory_query_only,
)
from trading_agent.sec_edgar_schema import (
    SEC_EDGAR_SCHEMA,
    SEC_EDGAR_SCHEMA_OBJECTS,
    SEC_EDGAR_SCHEMA_VERSION,
)
from trading_agent.sec_edgar_store_semantics import require_store_semantics
from trading_agent.sec_edgar_store_types import InvalidSecEdgarStoreError
from trading_agent.sec_edgar_store_version_chain import require_all_version_chains
from trading_agent.sqlite_uri import sqlite_read_only_uri, sqlite_read_write_uri


def _schema_signature(connection: sqlite3.Connection) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    )


def _expected_schema_signature() -> tuple[tuple[str, str, str, str], ...]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(SEC_EDGAR_SCHEMA)
        return _schema_signature(connection)


_EXPECTED_SCHEMA_SIGNATURE = _expected_schema_signature()


@contextmanager
def sec_writer(path: Path) -> Iterator[sqlite3.Connection]:
    try:
        parent_descriptor = open_private_parent(path.parent, create=True)
    except (OSError, TypeError, ValueError):
        raise InvalidSecEdgarStoreError from None
    try:
        require_private_directory(parent_descriptor)
        file_descriptor = _open_database_file(path, parent_descriptor, write=True)
        try:
            connection = sqlite3.connect(sqlite_read_write_uri(path), uri=True, timeout=0.0)
            try:
                _require_bound_database(path, parent_descriptor, file_descriptor)
                _ = connection.execute("PRAGMA foreign_keys = ON")
                _prepare(connection)
                _require_bound_database(path, parent_descriptor, file_descriptor)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                    _require_bound_database(path, parent_descriptor, file_descriptor)
                    connection.commit()
                    _require_bound_database(path, parent_descriptor, file_descriptor)
                except BaseException:
                    connection.rollback()
                    raise
            finally:
                connection.close()
        finally:
            os.close(file_descriptor)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        raise InvalidSecEdgarStoreError from None
    finally:
        os.close(parent_descriptor)


@contextmanager
def sec_reader(path: Path) -> Iterator[sqlite3.Connection]:
    try:
        parent_descriptor = open_private_parent(path.parent, create=False)
    except (OSError, TypeError, ValueError):
        raise InvalidSecEdgarStoreError from None
    try:
        require_private_directory_query_only(parent_descriptor)
        file_descriptor = _open_database_file(path, parent_descriptor, write=False)
        try:
            with closing(sqlite3.connect(sqlite_read_only_uri(path), uri=True)) as connection:
                _require_bound_database(path, parent_descriptor, file_descriptor)
                _ = connection.execute("PRAGMA query_only = ON")
                _ = connection.execute("PRAGMA foreign_keys = ON")
                _require_schema(connection)
                yield connection
                _require_bound_database(path, parent_descriptor, file_descriptor)
        finally:
            os.close(file_descriptor)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        raise InvalidSecEdgarStoreError from None
    finally:
        os.close(parent_descriptor)


def _prepare(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() == (0,):
        if _schema_signature(connection):
            raise InvalidSecEdgarStoreError
        connection.executescript(
            f"BEGIN IMMEDIATE;{SEC_EDGAR_SCHEMA}PRAGMA user_version={SEC_EDGAR_SCHEMA_VERSION};COMMIT;"
        )
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (SEC_EDGAR_SCHEMA_VERSION,):
        raise InvalidSecEdgarStoreError
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    )
    if (
        objects != SEC_EDGAR_SCHEMA_OBJECTS
        or _schema_signature(connection) != _EXPECTED_SCHEMA_SIGNATURE
        or connection.execute("PRAGMA foreign_keys").fetchone() != (1,)
        or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
        or connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
    ):
        raise InvalidSecEdgarStoreError
    require_all_version_chains(connection)
    require_store_semantics(connection)


def _open_database_file(path: Path, parent_descriptor: int, *, write: bool) -> int:
    flags = os.O_CLOEXEC | os.O_NOFOLLOW | (os.O_RDWR if write else os.O_RDONLY)
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
    except FileNotFoundError:
        if not write:
            raise
        descriptor = os.open(
            path.name,
            flags | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent_descriptor,
        )
        os.fchmod(descriptor, 0o600)
    try:
        _require_private_file_descriptor(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _require_bound_database(path: Path, parent_descriptor: int, file_descriptor: int) -> None:
    require_open_directory_path(path.parent, parent_descriptor)
    named = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
    opened = os.fstat(file_descriptor)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise InvalidSecEdgarStoreError
    _require_private_file_descriptor(file_descriptor)


def _require_private_file_descriptor(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidSecEdgarStoreError
