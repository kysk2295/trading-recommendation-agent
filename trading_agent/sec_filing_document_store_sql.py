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
from trading_agent.sec_filing_document_schema import (
    SEC_FILING_DOCUMENT_SCHEMA,
    SEC_FILING_DOCUMENT_SCHEMA_OBJECTS,
    SEC_FILING_DOCUMENT_SCHEMA_VERSION,
)
from trading_agent.sqlite_uri import sqlite_read_only_uri, sqlite_read_write_uri


class InvalidSecFilingDocumentStoreError(ValueError):
    def __str__(self) -> str:
        return "SEC filing document store is invalid"


def _schema_signature(connection: sqlite3.Connection) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    )


def _expected_schema_signature() -> tuple[tuple[str, str, str, str], ...]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(SEC_FILING_DOCUMENT_SCHEMA)
        return _schema_signature(connection)


_EXPECTED_SCHEMA_SIGNATURE = _expected_schema_signature()


@contextmanager
def document_writer(path: Path) -> Iterator[sqlite3.Connection]:
    try:
        parent = open_private_parent(path.parent, create=True)
        try:
            require_private_directory(parent)
            descriptor = _open_file(path, parent, write=True)
            try:
                with closing(sqlite3.connect(sqlite_read_write_uri(path), uri=True, timeout=0.0)) as connection:
                    _require_bound(path, parent, descriptor)
                    _ = connection.execute("PRAGMA foreign_keys = ON")
                    _prepare(connection)
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        yield connection
                        _require_bound(path, parent, descriptor)
                        connection.commit()
                    except BaseException:
                        connection.rollback()
                        raise
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        raise InvalidSecFilingDocumentStoreError from None


@contextmanager
def document_reader(path: Path) -> Iterator[sqlite3.Connection]:
    try:
        parent = open_private_parent(path.parent, create=False)
        try:
            require_private_directory_query_only(parent)
            descriptor = _open_file(path, parent, write=False)
            try:
                with closing(sqlite3.connect(sqlite_read_only_uri(path), uri=True)) as connection:
                    _require_bound(path, parent, descriptor)
                    _ = connection.execute("PRAGMA query_only = ON")
                    _ = connection.execute("PRAGMA foreign_keys = ON")
                    _require_schema(connection)
                    yield connection
                    _require_bound(path, parent, descriptor)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        raise InvalidSecFilingDocumentStoreError from None


def _prepare(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() == (0,):
        if connection.execute("SELECT name FROM sqlite_master").fetchone() is not None:
            raise InvalidSecFilingDocumentStoreError
        connection.executescript(
            f"{SEC_FILING_DOCUMENT_SCHEMA}PRAGMA user_version={SEC_FILING_DOCUMENT_SCHEMA_VERSION};"
        )
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    )
    if (
        connection.execute("PRAGMA user_version").fetchone() != (SEC_FILING_DOCUMENT_SCHEMA_VERSION,)
        or objects != SEC_FILING_DOCUMENT_SCHEMA_OBJECTS
        or _schema_signature(connection) != _EXPECTED_SCHEMA_SIGNATURE
        or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
        or connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
    ):
        raise InvalidSecFilingDocumentStoreError


def _open_file(path: Path, parent: int, *, write: bool) -> int:
    flags = os.O_CLOEXEC | os.O_NOFOLLOW | (os.O_RDWR if write else os.O_RDONLY)
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent)
    except FileNotFoundError:
        if not write:
            raise
        descriptor = os.open(path.name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
    _require_file(descriptor)
    return descriptor


def _require_bound(path: Path, parent: int, descriptor: int) -> None:
    require_open_directory_path(path.parent, parent)
    named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise InvalidSecFilingDocumentStoreError
    _require_file(descriptor)


def _require_file(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidSecFilingDocumentStoreError
